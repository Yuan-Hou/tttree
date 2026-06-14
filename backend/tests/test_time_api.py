"""时间控制 + 节点上下文 HTTP 壳(M5-B)。retry 需真 LLM,这里只覆盖确定性的
contexts / rollback / fork;retry 的底层逻辑由 test_retry.py 覆盖。"""

import json

import httpx
import pytest

from app.db.models import Blackboard, DrawProposal, ImageGen, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.stories.store import create_story


@pytest.fixture
async def ctx(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'time.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    from app.main import app
    from app.web.deps import get_session

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, Session
    app.dependency_overrides.clear()
    await engine.dispose()


def _bb(scenes, current=""):
    return {"story_meta": {"title": "T", "current_scene": current, "latest_beat": ""},
            "scenes": scenes, "characters": {}, "items": {}, "notes": []}


async def _add_turn(Session, sid, idx, *, bb, narrative="叙事", a_msgs=None, w_msgs=None, b_msgs=None):
    async with Session() as s:
        s.add(Turn(
            story_id=sid, turn_index=idx, beat_title=f"拍{idx}", user_input=f"行动{idx}",
            narrative=narrative, director_a_json=json.dumps({"writing_brief": "brief"}),
            director_b_json="{}", blackboard_after=json.dumps(bb),
            director_a_messages=json.dumps(a_msgs or [{"role": "system", "content": "A系统"}]),
            writer_messages=json.dumps(w_msgs or [{"role": "user", "content": "W输入"}]),
            director_b_messages=json.dumps(b_msgs or [{"role": "user", "content": "B输入"}]),
        ))
        await s.commit()


async def test_contexts_returns_messages_and_outputs(ctx):
    c, Session = ctx
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    bb = _bb({"room": {"name": "房间", "origin_turn": 1, "image_paths": []}}, current="room")
    await _add_turn(Session, sid, 1, bb=bb, narrative="你推开门。")

    r = await c.get(f"/story/{sid}/turn/1/contexts")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["turn_index"] == 1 and j["user_input"] == "行动1"
    assert j["director_a"]["messages"][0]["content"] == "A系统"
    assert j["director_a"]["output"]["writing_brief"] == "brief"
    assert j["writer"]["output"] == "你推开门。"
    assert j["director_b"]["output"]["scenes"]["room"]["name"] == "房间"

    assert (await c.get(f"/story/{sid}/turn/9/contexts")).status_code == 404


async def test_rollback_removes_latest_and_restores_blackboard(ctx):
    c, Session = ctx
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    bb1 = _bb({"a": {"name": "甲", "origin_turn": 1, "image_paths": []}}, current="a")
    bb2 = _bb({"a": {"name": "甲", "origin_turn": 1, "image_paths": []},
               "b": {"name": "乙", "origin_turn": 2, "image_paths": []}}, current="b")
    await _add_turn(Session, sid, 1, bb=bb1)
    await _add_turn(Session, sid, 2, bb=bb2)
    # 黑板当前 = 第2轮结束态
    async with Session() as s:
        row = await s.get(Blackboard, sid)
        row.json_blob = json.dumps(bb2)
        await s.commit()

    r = await c.post(f"/story/{sid}/rollback")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["rolled_back_turn"] == 2 and j["new_latest_turn"] == 1
    assert "b" in j["released_scene_slugs"]              # 第2轮诞生的场景被解除
    assert "b" not in (j["blackboard"]["scenes"] or {})  # 黑板回到第1轮:只剩甲
    # 第2轮 Turn 已删
    assert (await c.get(f"/story/{sid}/turn/2/contexts")).status_code == 404
    # 可连续回退到首轮
    assert (await c.post(f"/story/{sid}/rollback")).json()["rolled_back_turn"] == 1


async def test_edit_step_context_latest_only_and_isolated(ctx):
    """编辑最新轮某步输入 → 落盘且只改该列;历史轮编辑 → 409;上游输出记录不动。"""
    c, Session = ctx
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await _add_turn(Session, sid, 1, bb=_bb({}))
    await _add_turn(Session, sid, 2, bb=_bb({}))

    # 编辑最新轮(2)的 writer 输入记录
    new_msgs = [{"role": "system", "content": "改过的写手系统"}, {"role": "user", "content": "改过的brief"}]
    r = await c.put(f"/story/{sid}/turn/2/contexts/writer", json={"messages": new_msgs})
    assert r.status_code == 200, r.text

    ctxs = (await c.get(f"/story/{sid}/turn/2/contexts")).json()
    assert ctxs["writer"]["messages"][0]["content"] == "改过的写手系统"  # writer 输入已改
    # 改不动信息源:A 的输入记录 + A 的输出都没动
    assert ctxs["director_a"]["messages"][0]["content"] == "A系统"
    assert ctxs["director_a"]["output"]["writing_brief"] == "brief"
    # Writer 的输出(narrative)也没被编辑输入这件事改动
    assert ctxs["writer"]["output"] == "叙事"

    # 历史轮(1)不可编辑
    assert (await c.put(f"/story/{sid}/turn/1/contexts/writer", json={"messages": new_msgs})).status_code == 409
    # 非法 step
    assert (await c.put(f"/story/{sid}/turn/2/contexts/reducer", json={"messages": []})).status_code == 400


async def test_turn_draws_from_proposal_table_same_source(ctx):
    """按轮 draws 来自 DrawProposal(与绘图台同源):只列该轮提案,done 带缩略图。"""
    c, Session = ctx
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        ig = ImageGen(story_id=sid, scene_slug="room", kind="new_scene",
                      output_path="storage/images/r.png", source_turn=1)
        s.add(ig)
        await s.flush()
        s.add(DrawProposal(story_id=sid, scene_slug="room", origin_proposal_turn=1, kind="new_scene",
                           status="done", done_image_id=ig.id))
        s.add(DrawProposal(story_id=sid, scene_slug="hall", origin_proposal_turn=1, kind="new_scene", status="pending"))
        s.add(DrawProposal(story_id=sid, scene_slug="room", origin_proposal_turn=2, kind="variant", status="pending"))
        await s.commit()

    j = (await c.get(f"/story/{sid}/turn/1/draws")).json()
    # 只含第1轮的两条;第2轮那条不出现
    assert {(p["scene_slug"], p["kind"], p["status"]) for p in j["proposals"]} == {
        ("room", "new_scene", "done"), ("hall", "new_scene", "pending")}
    done = next(p for p in j["proposals"] if p["status"] == "done")
    assert done["done_image_path"] == "storage/images/r.png" and done["id"] > 0


async def test_story_proposals_aggregated_by_scene(ctx):
    """绘图台聚合:跨轮提案 + done 缩略图 + 场景 has_new_scene 门控位。"""
    c, Session = ctx
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        row = await s.get(Blackboard, sid)
        row.json_blob = json.dumps(_bb({"room": {"name": "房间", "origin_turn": 1}}, current="room"))
        ig = ImageGen(story_id=sid, scene_slug="room", kind="new_scene",
                      output_path="storage/images/r.png", source_turn=1)
        s.add(ig)
        await s.flush()
        s.add(DrawProposal(story_id=sid, scene_slug="room", origin_proposal_turn=1, kind="new_scene",
                           status="done", done_image_id=ig.id))
        s.add(DrawProposal(story_id=sid, scene_slug="room", origin_proposal_turn=3, kind="variant", status="pending"))
        await s.commit()

    j = (await c.get(f"/story/{sid}/proposals")).json()
    rooms = [p for p in j["proposals"] if p["scene_slug"] == "room"]
    assert {p["origin_proposal_turn"] for p in rooms} == {1, 3}
    done = next(p for p in rooms if p["status"] == "done")
    assert done["done_image_path"] == "storage/images/r.png"
    assert j["scenes"]["room"]["has_new_scene"] is True and j["scenes"]["room"]["name"] == "房间"


async def test_fork_appears_in_shelf(ctx):
    c, Session = ctx
    async with Session() as s:
        sid = (await create_story(s, title="原作")).id
    await _add_turn(Session, sid, 1, bb=_bb({}))

    r = await c.post(f"/stories/{sid}/fork")
    assert r.status_code == 200, r.text
    forked = r.json()
    assert forked["id"] != sid and "副本" in forked["title"]

    shelf = (await c.get("/stories")).json()
    ids = {s["id"] for s in shelf}
    assert sid in ids and forked["id"] in ids       # 原作 + 副本都在书架
