"""修复一:重绘后旧图自动降级,退出 Agent 候选池。

「取代」以「同场景同轮次」为单位:同一场景在某一轮里只有一张有效正典图,该轮对该场景的后续
重绘把前一张标记 superseded。被取代的图:退出 build_history_catalog(Agent 候选池)、仍留在黑板
image_paths(gallery)、仍可 RefPicker 手动选(全列、不过滤)。跨轮各自独立、互不影响。
"""

import json

from sqlalchemy import select

from app.db.models import Blackboard, ImageGen
from app.db.session import create_all, make_engine, make_session_factory
from app.imaging.draw_service import DraftBundle, apply_decision, build_history_catalog
from app.imaging.executor import ExecResult, ResolvedRefs
from app.models.schemas import IllustratorDraft
from app.stories.store import create_story


def _bb(scenes: dict) -> str:
    return json.dumps(
        {"story_meta": {"title": "T"}, "scenes": scenes, "characters": {}, "items": {}, "notes": []},
        ensure_ascii=False,
    )


def _scene_X():
    return {"X": {"name": "场景X", "state": "", "image_paths": [], "origin_turn": 1}}


def _bundle(kind="variant"):
    return DraftBundle(
        scene_slug="X",
        draft=IllustratorDraft(kind=kind, prompt_text="p", reference_manifest=[]),
        resolved=ResolvedRefs(),
        history=[],
    )


async def _story_with_scene_X(Session) -> str:
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        bb = await s.get(Blackboard, sid)
        bb.json_blob = _bb(_scene_X())
        await s.commit()
    return sid


async def _draw_canon(Session, sid, *, source_turn) -> dict:
    async with Session() as s:
        return await apply_decision(
            s, decision="confirm", bundle=_bundle(), final_prompt="p",
            story_id=sid, origin="director_b_proposal", source_turn=source_turn,
        )


async def test_same_turn_redraw_supersedes_old(tmp_path, monkeypatch):
    """同一轮对同场景连续画三次 → 第三张有效;前两张 superseded、退出候选池,
    但仍在 image_paths、RefPicker(全列)仍可见。"""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'sup.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    sid = await _story_with_scene_X(Session)

    outs = iter([f"storage/images/x{i}.png" for i in range(1, 4)])

    async def fake_exec(*, final_prompt, ref_files, scene_slug, **kw):
        return ExecResult(output_path=next(outs), api_call="generate", ref_files_sent=[])

    monkeypatch.setattr("app.imaging.draw_service.execute_image", fake_exec)

    ids = [(await _draw_canon(Session, sid, source_turn=1))["imagegen_id"] for _ in range(3)]

    async with Session() as s:
        rows = {ig.id: ig for ig in (await s.execute(select(ImageGen))).scalars().all()}
        bb = json.loads((await s.get(Blackboard, sid)).json_blob)
        cat = await build_history_catalog(s, sid, "X", "场景X")
        all_paths = [
            ig.output_path
            for ig in (await s.execute(select(ImageGen).where(ImageGen.output_path != ""))).scalars().all()
        ]

    # 前两张被取代,第三张有效
    assert rows[ids[0]].superseded is True
    assert rows[ids[1]].superseded is True
    assert rows[ids[2]].superseded is False
    # 候选池只剩最新一张
    pool = [c["image_path"] for c in cat]
    assert pool == ["storage/images/x3.png"]
    # 但三张都还在 image_paths(gallery 可翻页)
    assert bb["scenes"]["X"]["image_paths"] == [
        "storage/images/x1.png", "storage/images/x2.png", "storage/images/x3.png",
    ]
    # RefPicker 过往结果(全列、不过滤)仍含被取代的图
    assert all_paths == bb["scenes"]["X"]["image_paths"]
    await engine.dispose()


async def test_cross_turn_redraws_independent(tmp_path, monkeypatch):
    """跨轮重绘:不同轮各自轮次内的最新图互不影响 —— 都不被取代、都在候选池。"""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'sup2.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    sid = await _story_with_scene_X(Session)

    outs = iter(["storage/images/t1.png", "storage/images/t2.png"])

    async def fake_exec(*, final_prompt, ref_files, scene_slug, **kw):
        return ExecResult(output_path=next(outs), api_call="generate", ref_files_sent=[])

    monkeypatch.setattr("app.imaging.draw_service.execute_image", fake_exec)

    id1 = (await _draw_canon(Session, sid, source_turn=1))["imagegen_id"]
    id2 = (await _draw_canon(Session, sid, source_turn=2))["imagegen_id"]

    async with Session() as s:
        rows = {ig.id: ig for ig in (await s.execute(select(ImageGen))).scalars().all()}
        cat = await build_history_catalog(s, sid, "X", "场景X")

    assert rows[id1].superseded is False and rows[id2].superseded is False
    pool = {c["image_path"] for c in cat}
    assert pool == {"storage/images/t1.png", "storage/images/t2.png"}
    await engine.dispose()
