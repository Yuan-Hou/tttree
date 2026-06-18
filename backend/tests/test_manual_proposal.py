"""手动指定绘图(用户自建提案):为「任意场景 × 任意轮」自建一条 DrawProposal,作者是用户。

验证:① 自建提案落 DrawProposal(origin_proposal_turn=N、kind 按诞生点权威、reason 标手动)→
随即出现在绘图台 GET /proposals,且在该轮 GET /turn/N/draws(工作台绘图分支同源)可见;
② kind 判定:诞生轮画=new_scene,后续轮画=variant;③ 校验:场景不在该轮黑板 → 400、轮不存在 → 404;
④ GET /turn/N/scenes 列出该轮可画场景 + 各自 kind / variant 门控。
"""

import json

import httpx
from sqlalchemy import select

from app.db.models import Blackboard, DrawProposal, ImageGen, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.stories.store import create_story


def _bb(scenes: dict) -> str:
    return json.dumps(
        {"story_meta": {"title": "T"}, "scenes": scenes, "characters": {}, "items": {}, "notes": []},
        ensure_ascii=False,
    )


async def _setup(tmp_path, monkeypatch):
    """建故事 + 两轮:第1轮诞生场景 A;第2轮 A 仍在、并诞生场景 B。"""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'mp.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.web.draw_router.async_session", Session)
    monkeypatch.setattr("app.db.session.async_session", Session)  # GET /proposals 经 deps.get_session

    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        bb1 = _bb({"A": {"name": "场景A", "state": "", "image_paths": [], "origin_turn": 1}})
        bb2 = _bb({
            "A": {"name": "场景A", "state": "", "image_paths": [], "origin_turn": 1},
            "B": {"name": "场景B", "state": "", "image_paths": [], "origin_turn": 2},
        })
        (await s.get(Blackboard, sid)).json_blob = bb2  # 当前黑板=第2轮后
        s.add(Turn(story_id=sid, turn_index=1, user_input="u1", narrative="n1",
                   director_a_json="{}", director_b_json="{}", blackboard_after=bb1))
        s.add(Turn(story_id=sid, turn_index=2, user_input="u2", narrative="n2",
                   director_a_json="{}", director_b_json="{}", blackboard_after=bb2))
        await s.commit()
    return engine, Session, sid


async def test_manual_proposal_canon_and_visible_both_facets(tmp_path, monkeypatch):
    """自建提案 → 落库 origin_proposal_turn=N、reason 标手动;在绘图台与该轮工作台分支两个切面都可见。"""
    engine, Session, sid = await _setup(tmp_path, monkeypatch)
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # 在第1轮(A 的诞生轮)手动指定画 A → new_scene
        r = await c.post(f"/story/{sid}/proposal", json={"scene": "A", "turn": 1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "new_scene" and body["origin_proposal_turn"] == 1
        assert "手动" in body["reason"]
        pid = body["id"]

        # 切面一:绘图台(按场景,跨轮积压)
        props = (await c.get(f"/story/{sid}/proposals")).json()["proposals"]
        assert any(p["id"] == pid and p["scene_slug"] == "A" for p in props)

        # 切面二:该轮工作台绘图分支(按轮,与绘图台同源)
        draws = (await c.get(f"/story/{sid}/turn/1/draws")).json()["proposals"]
        assert any(p["id"] == pid for p in draws)
        # 不串轮:第2轮的分支里不该有它
        draws2 = (await c.get(f"/story/{sid}/turn/2/draws")).json()["proposals"]
        assert all(p["id"] != pid for p in draws2)

    async with Session() as s:
        prop = await s.get(DrawProposal, pid)
    assert prop.status == "pending" and prop.kind == "new_scene"
    await engine.dispose()


async def test_manual_proposal_kind_by_birthpoint(tmp_path, monkeypatch):
    """kind 按场景诞生点:A 在第2轮(>诞生轮1)画 → variant;B 在其诞生轮2画 → new_scene。"""
    engine, Session, sid = await _setup(tmp_path, monkeypatch)
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        rA = await c.post(f"/story/{sid}/proposal", json={"scene": "A", "turn": 2})
        rB = await c.post(f"/story/{sid}/proposal", json={"scene": "B", "turn": 2})
    assert rA.json()["kind"] == "variant"
    assert rB.json()["kind"] == "new_scene"
    await engine.dispose()


async def test_manual_proposal_rejects_scene_absent_or_turn_missing(tmp_path, monkeypatch):
    """校验:B 不在第1轮黑板 → 400;轮 99 不存在 → 404。"""
    engine, Session, sid = await _setup(tmp_path, monkeypatch)
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r_absent = await c.post(f"/story/{sid}/proposal", json={"scene": "B", "turn": 1})
        r_noturn = await c.post(f"/story/{sid}/proposal", json={"scene": "A", "turn": 99})
    assert r_absent.status_code == 400
    assert r_noturn.status_code == 404
    await engine.dispose()


async def test_turn_scenes_lists_drawable_with_kind_and_gating(tmp_path, monkeypatch):
    """GET /turn/N/scenes:第1轮只 A(new_scene);第2轮 A(variant,缺基底→gated)+ B(new_scene)。"""
    engine, Session, sid = await _setup(tmp_path, monkeypatch)
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        s1 = (await c.get(f"/story/{sid}/turn/1/scenes")).json()["scenes"]
        s2 = (await c.get(f"/story/{sid}/turn/2/scenes")).json()["scenes"]
    assert {x["slug"]: x["kind"] for x in s1} == {"A": "new_scene"}
    by2 = {x["slug"]: x for x in s2}
    assert by2["A"]["kind"] == "variant" and by2["A"]["variant_gated"] is True  # 无基底 → 门控
    assert by2["B"]["kind"] == "new_scene" and by2["B"]["variant_gated"] is False
    await engine.dispose()
