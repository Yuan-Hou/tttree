"""绘图执行的类型/截断/不限轮/门控/警告(M5-B 绘图语义升级·子步二)。

draw_router 用 async_session() 直连(非 Depends),故 monkeypatch 它指向临时库;
run_illustrator/execute_image 也 monkeypatch 掉(不打真 LLM/不出图),并借 run_illustrator
捕获「喂给绘图 Agent 的截断上下文」。
"""

import json

import httpx
import pytest
from sqlalchemy import select

from app.db.models import DrawProposal, ImageGen, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.models.schemas import IllustratorDraft
from app.stories.store import create_story


def _bb(scenes: dict) -> str:
    return json.dumps({"story_meta": {"title": "T"}, "scenes": scenes, "characters": {}, "items": {}, "notes": []})


def _scene(origin: int, state: str = "") -> dict:
    return {"name": "场景", "state": state, "image_paths": [], "origin_turn": origin}


@pytest.fixture
async def env(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'ds.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.web.draw_router.async_session", Session)

    captured: dict = {}

    async def fake_illustrator(*, history, blackboard, draw_request, reference_catalog, visual_style=None, messages=None, model=None, tips=None, extra_instruction=None):
        captured["history"] = history
        captured["blackboard"] = blackboard
        captured["draw_request"] = draw_request
        # 故意返回 variant,验证后端按提案的权威 kind 覆盖
        return IllustratorDraft(kind="variant", prompt_text="稿", reference_manifest=[])

    monkeypatch.setattr("app.imaging.draw_service.run_illustrator", fake_illustrator)

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, Session, captured
    await engine.dispose()


async def _seed_five_turns(Session, sid):
    """X 诞生于第2轮;第3轮引入 Y。每轮 blackboard_after 不同,用于验证截断。"""
    bbs = {
        1: _bb({"intro": _scene(1)}),
        2: _bb({"intro": _scene(1), "X": _scene(2, "initial")}),
        3: _bb({"intro": _scene(1), "X": _scene(2, "later"), "Y": _scene(3)}),
        4: _bb({"intro": _scene(1), "X": _scene(2, "later"), "Y": _scene(3)}),
        5: _bb({"intro": _scene(1), "X": _scene(2, "later"), "Y": _scene(3)}),
    }
    async with Session() as s:
        for i in range(1, 6):
            s.add(Turn(story_id=sid, turn_index=i, user_input=f"行动{i}", narrative=f"叙事{i}",
                       director_a_json="{}", director_b_json="{}", blackboard_after=bbs[i]))
        await s.commit()


async def test_draw_old_proposal_kind_and_truncation(env):
    """① 在「最新轮=5」时画第2轮积压提案 → kind=new_scene(按 origin_turn)、上下文截断到第2轮、不限最新轮。"""
    c, Session, captured = env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await _seed_five_turns(Session, sid)
    async with Session() as s:
        s.add(DrawProposal(story_id=sid, scene_slug="X", origin_proposal_turn=2, kind="new_scene", status="pending"))
        await s.commit()
        pid = (await s.execute(select(DrawProposal.id))).scalar_one()

    r = await c.post(f"/story/{sid}/draw", json={"proposal_id": pid})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["kind"] == "new_scene"  # 后端权威覆盖了 illustrator 自报的 variant
    assert j["draw_turn"] == 2 and j["warn_redraw_base"] is False

    # 上下文截断到第2轮:对话只到 turn2(4 段),黑板是 turn2 的(X.state=initial、且无 turn3 才出现的 Y)
    assert len(captured["history"]) == 4  # turns 1,2 各 user+assistant
    assert captured["history"][-1]["content"] == "叙事2"
    scenes = captured["blackboard"]["scenes"]
    assert scenes["X"]["state"] == "initial" and "Y" not in scenes


async def test_variant_gated_without_base(env):
    """② 场景无 new_scene 基底 → variant 提案不可执行(409)。"""
    c, Session, _ = env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await _seed_five_turns(Session, sid)
    async with Session() as s:  # X 召回轮的 variant 提案,但 X 尚无任何已画图
        s.add(DrawProposal(story_id=sid, scene_slug="X", origin_proposal_turn=3, kind="variant", status="pending"))
        await s.commit()
        pid = (await s.execute(select(DrawProposal.id))).scalar_one()

    r = await c.post(f"/story/{sid}/draw", json={"proposal_id": pid})
    assert r.status_code == 409, r.text


async def test_redraw_new_scene_warns_when_variant_exists(env):
    """③ 场景已有 variant 时,重绘 new_scene → warn_redraw_base=true(不阻断,仅告警)。"""
    c, Session, _ = env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await _seed_five_turns(Session, sid)
    async with Session() as s:
        s.add(ImageGen(story_id=sid, scene_slug="X", kind="new_scene", output_path="storage/images/a.png", source_turn=2))
        s.add(ImageGen(story_id=sid, scene_slug="X", kind="variant", output_path="storage/images/b.png", source_turn=4))
        s.add(DrawProposal(story_id=sid, scene_slug="X", origin_proposal_turn=2, kind="new_scene", status="pending"))
        await s.commit()
        pid = (await s.execute(select(DrawProposal.id).where(DrawProposal.kind == "new_scene"))).scalar_one()

    r = await c.post(f"/story/{sid}/draw", json={"proposal_id": pid})
    assert r.status_code == 200 and r.json()["warn_redraw_base"] is True


async def test_confirm_marks_proposal_done(env, monkeypatch):
    """画完(confirm)→ 对应 DrawProposal status=done、done_image_id 指向 ImageGen。"""
    from app.imaging.executor import ExecResult

    async def fake_execute(*, final_prompt, ref_files, scene_slug, **kw):
        return ExecResult(output_path="storage/images/x.png", api_call="generate", ref_files_sent=[])

    monkeypatch.setattr("app.imaging.draw_service.execute_image", fake_execute)

    c, Session, _ = env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await _seed_five_turns(Session, sid)
    async with Session() as s:
        s.add(DrawProposal(story_id=sid, scene_slug="X", origin_proposal_turn=2, kind="new_scene", status="pending"))
        await s.commit()
        pid = (await s.execute(select(DrawProposal.id))).scalar_one()

    draft = (await c.post(f"/story/{sid}/draw", json={"proposal_id": pid})).json()
    r = await c.post(f"/story/{sid}/draw/confirm", json={"draft_id": draft["draft_id"], "decision": "confirm"})
    assert r.status_code == 200
    _ = r.text  # 读完流 → 生成器跑完(含 mark done)

    async with Session() as s:
        p = await s.get(DrawProposal, pid)
        ig = (await s.execute(select(ImageGen).where(ImageGen.story_id == sid))).scalar_one()
    assert p.status == "done" and p.done_image_id == ig.id
