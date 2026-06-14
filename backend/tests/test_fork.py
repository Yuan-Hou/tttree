import json

from sqlalchemy import func, select

from app.db.models import Blackboard, ImageGen, ReferenceAsset, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.imaging.pipeline import record_generation
from app.knowledge.store import get_knowledge, set_knowledge
from app.state.reducer import reduce_turn
from app.stories.store import create_story, fork_story


def _scene(name: str, image_paths=None) -> dict:
    return {"name": name, "base_prompt": "", "visual_anchors": [], "state": "",
            "connections": [], "image_paths": image_paths or []}


def _bb(scenes: dict, current: str) -> str:
    return json.dumps({"story_meta": {"title": "原档", "current_scene": current, "latest_beat": ""},
                       "scenes": scenes, "characters": {}, "items": {}, "notes": []}, ensure_ascii=False)


async def _setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'fork.db'}")
    await create_all(engine)
    return make_session_factory(engine)


async def _count(Session, model, sid):
    async with Session() as s:
        return (await s.execute(select(func.count()).select_from(model).where(model.story_id == sid))).scalar()


async def _bb_of(Session, sid):
    async with Session() as s:
        return json.loads((await s.get(Blackboard, sid)).json_blob)


async def test_fork_is_complete_independent_and_shares_files(tmp_path):
    Session = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="原档")).id

    # 轮1(带每步上下文)→ room
    async with Session() as s:
        await reduce_turn(story_id=sid, director_b_new_blackboard_str=_bb({"room": _scene("房间")}, "room"),
                          writer_narrative="n1", director_a_json="{}", user_input="u1", session=s,
                          director_a_messages='[{"role":"system","content":"A轮1"}]',
                          writer_messages='[{"role":"system","content":"W轮1"}]',
                          director_b_messages='[{"role":"system","content":"B轮1"}]')
    # 参考图 + 一张引用了该参考图的生成图(磁盘路径共享)
    async with Session() as s:
        ref = ReferenceAsset(story_id=sid, label="主角立绘", description="", category="角色",
                             file_path="storage/references/p.png")
        s.add(ref)
        await s.flush()
        ref_id = ref.id
        await s.commit()
    rel = "storage/images/room.png"
    async with Session() as s:
        await record_generation(s, story_id=sid, scene_slug="room", kind="new_scene", final_prompt="p",
                                ref_asset_ids=[ref_id], ref_image_paths=[], output_path=rel,
                                origin="user_initiated", source_turn=1)
    # 轮2(保留图引用)+ 知识库
    async with Session() as s:
        await reduce_turn(story_id=sid,
                          director_b_new_blackboard_str=_bb({"room": _scene("房间", [rel])}, "room"),
                          writer_narrative="n2", director_a_json="{}", user_input="u2", session=s)
        await set_knowledge(s, sid, "白子:阿拜多斯对策委员会,沉默寡言。")

    # ---- fork ----
    async with Session() as s:
        forked = await fork_story(s, sid)
        nsid = forked.id
    assert nsid != sid and forked.title == "原档(副本)"

    # 完整复制:各类数据都在
    assert await _bb_of(Session, nsid) == await _bb_of(Session, sid)          # 黑板
    async with Session() as s:
        assert await get_knowledge(s, nsid) == "白子:阿拜多斯对策委员会,沉默寡言。"  # 知识库
    assert await _count(Session, Turn, nsid) == await _count(Session, Turn, sid) == 2  # Turn
    assert await _count(Session, ImageGen, nsid) == await _count(Session, ImageGen, sid) == 1
    assert await _count(Session, ReferenceAsset, nsid) == 1
    # 每步上下文也复制了
    async with Session() as s:
        t1 = (await s.execute(select(Turn).where(Turn.story_id == nsid, Turn.turn_index == 1))).scalar_one()
        assert "A轮1" in t1.director_a_messages and "W轮1" in t1.writer_messages

    # 图片文件物理共享:路径与原档相同(不复制文件)
    async with Session() as s:
        ig_src = (await s.execute(select(ImageGen).where(ImageGen.story_id == sid))).scalar_one()
        ig_new = (await s.execute(select(ImageGen).where(ImageGen.story_id == nsid))).scalar_one()
        ref_new = (await s.execute(select(ReferenceAsset).where(ReferenceAsset.story_id == nsid))).scalar_one()
    assert ig_new.output_path == ig_src.output_path == rel          # 同一磁盘文件
    assert ref_new.file_path == "storage/references/p.png"          # 参考图文件共享
    assert (await _bb_of(Session, nsid))["scenes"]["room"]["image_paths"] == [rel]
    # ref_asset_ids 重映射到副本自己的参考图 id(不再指向原档的 ref 行)
    assert json.loads(ig_new.ref_asset_ids) == [ref_new.id]
    assert ref_new.id != ref_id

    # ---- 数据层独立:在副本里推进一轮,不影响原档 ----
    src_turns_before = await _count(Session, Turn, sid)
    src_bb_before = await _bb_of(Session, sid)
    async with Session() as s:
        await reduce_turn(story_id=nsid,
                          director_b_new_blackboard_str=_bb({"room": _scene("房间", [rel]), "loft": _scene("阁楼")}, "loft"),
                          writer_narrative="n3", director_a_json="{}", user_input="u3", session=s)
    assert await _count(Session, Turn, nsid) == 3                   # 副本推进到 3 轮
    assert await _count(Session, Turn, sid) == src_turns_before     # 原档轮数不变
    assert await _bb_of(Session, sid) == src_bb_before              # 原档黑板不变
    assert "loft" not in (await _bb_of(Session, sid))["scenes"]     # 副本的新场景没漏到原档

    # 反向:编辑原档黑板不影响副本
    async with Session() as s:
        row = await s.get(Blackboard, sid)
        bb = json.loads(row.json_blob)
        bb["story_meta"]["latest_beat"] = "原档独自改动"
        row.json_blob = json.dumps(bb, ensure_ascii=False)
        await s.commit()
    assert (await _bb_of(Session, nsid))["story_meta"]["latest_beat"] != "原档独自改动"
