import json

from sqlalchemy import func, select

from app.db.models import Blackboard, ImageGen, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.imaging.pipeline import record_generation
from app.state.reducer import reduce_turn
from app.stories.store import create_story
from app.turns.rollback import rollback_latest_turn


def _scene(name: str) -> dict:
    return {"name": name, "base_prompt": "", "visual_anchors": [], "state": "", "connections": [], "image_paths": []}


def _bb(scenes: dict, current: str, title: str = "回退测试") -> str:
    return json.dumps(
        {"story_meta": {"title": title, "current_scene": current, "latest_beat": ""},
         "scenes": scenes, "characters": {}, "items": {}, "notes": []},
        ensure_ascii=False,
    )


async def _setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'rb.db'}")
    await create_all(engine)
    return make_session_factory(engine)


async def _turn(Session, sid, b_str):
    async with Session() as s:
        return await reduce_turn(story_id=sid, director_b_new_blackboard_str=b_str,
                                 writer_narrative="n", director_a_json="{}", user_input="u", session=s)


async def _bb_now(Session, sid):
    async with Session() as s:
        return json.loads((await s.get(Blackboard, sid)).json_blob)


async def _imagegen_count(Session, sid, slug):
    async with Session() as s:
        return (await s.execute(
            select(func.count()).select_from(ImageGen).where(ImageGen.story_id == sid, ImageGen.scene_slug == slug)
        )).scalar()


async def test_rollback_scene_vanishes_assets_kept_and_can_continue(tmp_path):
    Session = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="回退测试")).id

    # 轮1: room;轮2: 诞生 cellar(X);轮3: 状态延续
    await _turn(Session, sid, _bb({"room": _scene("房间")}, "room"))
    await _turn(Session, sid, _bb({"room": _scene("房间"), "cellar": _scene("地窖")}, "cellar"))

    # 给 X(cellar)画一张图:真实 record_generation(追加黑板 image_paths + 建 ImageGen)+ 磁盘文件
    img_file = tmp_path / "cellar.png"
    img_file.write_bytes(b"PNGDATA")
    rel = str(img_file)
    async with Session() as s:
        await record_generation(s, story_id=sid, scene_slug="cellar", kind="new_scene", final_prompt="",
                                ref_asset_ids=[], ref_image_paths=[], output_path=rel,
                                origin="user_initiated", source_turn=2)

    await _turn(Session, sid, _bb(
        {"room": _scene("房间"), "cellar": {**_scene("地窖"), "image_paths": [rel]}}, "cellar"))

    # 现状:cellar 在、有图、ImageGen + 文件在
    bb = await _bb_now(Session, sid)
    assert "cellar" in bb["scenes"] and rel in bb["scenes"]["cellar"]["image_paths"]
    assert await _imagegen_count(Session, sid, "cellar") == 1
    assert img_file.exists()

    # 回退轮3 → cellar 诞生于轮2,不受影响
    async with Session() as s:
        r3 = await rollback_latest_turn(s, sid)
    assert r3.ok and r3.rolled_back_turn == 3 and r3.new_latest_turn == 2
    assert "cellar" in (await _bb_now(Session, sid))["scenes"]

    # 回退轮2(到 X 诞生轮之前)→ cellar 消失;图引用解除;资产保留
    async with Session() as s:
        r2 = await rollback_latest_turn(s, sid)
    assert r2.ok and r2.rolled_back_turn == 2 and r2.new_latest_turn == 1
    assert r2.released_scene_slugs == ["cellar"]
    assert r2.released_image_paths == [rel]

    async with Session() as s:
        turn1_after = json.loads(
            (await s.execute(select(Turn).where(Turn.story_id == sid, Turn.turn_index == 1))).scalar_one().blackboard_after
        )
        remaining = (await s.execute(
            select(Turn.turn_index).where(Turn.story_id == sid).order_by(Turn.turn_index)
        )).scalars().all()
    bb = await _bb_now(Session, sid)
    assert "cellar" not in bb["scenes"]          # 场景消失
    assert rel not in json.dumps(bb)             # 图引用随场景一并消失
    assert bb == turn1_after                      # 黑板 == 目标轮 blackboard_after
    assert remaining == [1]                       # 轮2、3 的 Turn 记录已删
    assert await _imagegen_count(Session, sid, "cellar") == 1  # ImageGen 记录仍在(资产保留)
    assert img_file.exists()                      # 磁盘文件仍在(资产保留)

    # 回退后可继续:正常推进新一轮,turn_index 复用 2,行为如常
    r = await _turn(Session, sid, _bb({"room": _scene("房间"), "loft": _scene("阁楼")}, "loft"))
    assert r.ok and r.turn_index == 2
    bb = await _bb_now(Session, sid)
    assert "loft" in bb["scenes"] and bb["scenes"]["loft"]["origin_turn"] == 2  # 新场景诞生点=复用的轮号


async def test_rollback_first_turn_restores_empty_and_consecutive(tmp_path):
    """连续回退到底:回退首轮 → 恢复初始空黑板,无可回退后返回 ok=False。"""
    Session = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="空回退")).id
    await _turn(Session, sid, _bb({"room": _scene("房间")}, "room", title="空回退"))
    await _turn(Session, sid, _bb({"room": _scene("房间"), "hall": _scene("大厅")}, "hall", title="空回退"))

    async with Session() as s:
        assert (await rollback_latest_turn(s, sid)).ok  # 回退轮2
    async with Session() as s:
        r1 = await rollback_latest_turn(s, sid)         # 回退轮1
    assert r1.ok and r1.new_latest_turn is None
    bb = await _bb_now(Session, sid)
    # 回到初始空黑板;标题不在黑板里(只是档案标记)
    assert bb["scenes"] == {} and "title" not in bb["story_meta"]
    async with Session() as s:
        none_left = await rollback_latest_turn(s, sid)  # 已无可回退
    assert none_left.ok is False


async def test_rollback_keeps_late_drawn_image_via_imagegen_rebuild(tmp_path):
    """回归:归属过去轮、但画在该轮快照冻结之后的正典图,回退更晚轮时不应丢(据 ImageGen 自愈)。

    根因:record_generation 只 append 进实时黑板,不写进任何轮的 blackboard_after;回退用旧快照
    覆盖实时黑板 → 这类「补画」的图被静默丢弃。修复后 rollback 据 ImageGen 重建 image_paths。
    """
    Session = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="补图回退")).id
    # 轮1 room;轮2 cellar 诞生(此刻 cellar 快照 image_paths=[])
    await _turn(Session, sid, _bb({"room": _scene("房间")}, "room", title="补图回退"))
    await _turn(Session, sid, _bb({"room": _scene("房间"), "cellar": _scene("地窖")}, "cellar", title="补图回退"))
    # 给 cellar 出一张正典图,归属轮2(append 进实时黑板;轮2 快照里没有)
    img = str(tmp_path / "cellar.png")
    async with Session() as s:
        await record_generation(s, story_id=sid, scene_slug="cellar", kind="new_scene", final_prompt="",
                                ref_asset_ids=[], ref_image_paths=[], output_path=img,
                                origin="director_b_proposal", source_turn=2)
    # 轮3 推进(reduce 从实时黑板承袭 → 轮3 带图)
    await _turn(Session, sid, _bb({"room": _scene("房间"), "cellar": _scene("地窖"), "hall": _scene("大厅")}, "hall", title="补图回退"))
    # 取证:轮2 的 blackboard_after 快照里 cellar 仍是 [](快照确实漏了这张图)
    async with Session() as s:
        t2 = (await s.execute(select(Turn).where(Turn.story_id == sid, Turn.turn_index == 2))).scalar_one()
    assert json.loads(t2.blackboard_after)["scenes"]["cellar"]["image_paths"] == []
    # 回退轮3 → 还原轮2快照,但据 ImageGen 自愈重建 → cellar 图仍在(修复前会变成 [])
    async with Session() as s:
        r = await rollback_latest_turn(s, sid)
    assert r.ok and r.new_latest_turn == 2
    bb = await _bb_now(Session, sid)
    assert bb["scenes"]["cellar"]["image_paths"] == [img]
