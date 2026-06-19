import json

from sqlalchemy import func, select

from app.db.models import Blackboard, DrawProposal, ImageGen, ReferenceAsset, Turn
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
                          director_b_messages='[{"role":"system","content":"B轮1"}]',
                          options_json='{"options":["选项甲","选项乙"]}',
                          options_messages='[{"role":"system","content":"O轮1"}]')
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
    # 副本标题含「第几拍」(派生时源故事 2 轮)+「第n个副本」(不重复的最小 n=1)
    assert nsid != sid and forked.title == "原档(第2拍第1个副本)"

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
        assert json.loads(t1.options_json)["options"] == ["选项甲", "选项乙"]  # Options 输出
        assert "O轮1" in t1.options_messages  # Options 上下文

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


async def test_fork_copies_draw_proposals_and_remaps_refs(tmp_path):
    """修复三:fork 必须完整复制绘图待办(DrawProposal,绘图台/工作台节点的数据源),
    且重映射所有跨记录引用:done_image_id→新 ImageGen.id、draft_manifest 的 asset_id→新参考图 id;
    并保留 ImageGen.superseded(被取代的旧图在副本里仍是被覆盖,不回到正典)。"""
    Session = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="原档")).id

    # 参考图(供 draft_manifest 引用 + 重映射验证)
    async with Session() as s:
        ref = ReferenceAsset(story_id=sid, label="主角立绘", description="", category="角色",
                             file_path="storage/references/p.png")
        s.add(ref)
        await s.flush()
        ref_id = ref.id
        await s.commit()

    # 同场景同轮先后两张正典图 → 第一张被自动取代(superseded=True),第二张有效
    async with Session() as s:
        await record_generation(s, story_id=sid, scene_slug="room", kind="new_scene", final_prompt="p1",
                                ref_asset_ids=[], ref_image_paths=[], output_path="storage/images/a1.png",
                                origin="director_b_proposal", source_turn=1)
    async with Session() as s:
        ig2 = await record_generation(s, story_id=sid, scene_slug="room", kind="new_scene", final_prompt="p2",
                                      ref_asset_ids=[], ref_image_paths=[], output_path="storage/images/a2.png",
                                      origin="director_b_proposal", source_turn=1)
        ig2_id = ig2.id

    # 绘图待办:已完成,指向最新有效图;manifest 同时含参考图项(需重映射)与历史图项(磁盘共享、不动)
    async with Session() as s:
        s.add(DrawProposal(
            story_id=sid, scene_slug="room", origin_proposal_turn=1, kind="new_scene", status="done",
            reason="配图理由", done_image_id=ig2_id, draft_prompt="提示词", draft_messages="[]",
            draft_manifest=json.dumps([
                {"source": "reference_asset", "asset_id": ref_id, "semantic_name": "主角", "purpose": "角色一致"},
                {"source": "history_image", "image_path": "storage/images/a1.png", "semantic_name": "上一张", "purpose": "连贯"},
            ], ensure_ascii=False),
        ))
        await s.commit()

    # ---- fork ----
    async with Session() as s:
        nsid = (await fork_story(s, sid)).id

    # DrawProposal 整张表复制到副本
    assert await _count(Session, DrawProposal, nsid) == 1
    async with Session() as s:
        ndp = (await s.execute(select(DrawProposal).where(DrawProposal.story_id == nsid))).scalar_one()
        new_ref = (await s.execute(select(ReferenceAsset).where(ReferenceAsset.story_id == nsid))).scalar_one()
        new_igs = (
            await s.execute(select(ImageGen).where(ImageGen.story_id == nsid).order_by(ImageGen.id))
        ).scalars().all()

    # superseded 随之复制:副本里仍是「一张被取代、一张有效」,而不是两张都回到正典
    assert [ig.superseded for ig in new_igs] == [True, False]
    # done_image_id 重映射到副本自己的新 ImageGen id(第二张),不再指向原档的 id
    assert ndp.done_image_id == new_igs[1].id
    assert ndp.done_image_id != ig2_id
    # draft_manifest:reference_asset 的 asset_id 重映射到副本参考图;history_image 路径原样共享
    man = json.loads(ndp.draft_manifest)
    assert man[0]["asset_id"] == new_ref.id and new_ref.id != ref_id
    assert man[1]["image_path"] == "storage/images/a1.png"
    # 其余字段原样保留
    assert ndp.status == "done" and ndp.kind == "new_scene" and ndp.reason == "配图理由"


async def test_fork_titles_include_beat_and_dedup_min_n(tmp_path):
    """副本标题含「第几拍」(派生时源轮数)+「第n个副本」(取不重复的最小 n);
    再 fork 副本时剥掉已有后缀、基名稳定不堆叠。"""
    Session = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="原档")).id
        for i in (1, 2, 3):  # 3 轮 → beat=3
            s.add(Turn(story_id=sid, turn_index=i, narrative=f"n{i}", user_input=f"u{i}"))
        await s.commit()

    async with Session() as s:
        f1 = await fork_story(s, sid)
    async with Session() as s:
        f2 = await fork_story(s, sid)  # 同源再 fork → n 取下一个最小值
    assert f1.title == "原档(第3拍第1个副本)"
    assert f2.title == "原档(第3拍第2个副本)"

    # fork 一个副本(它也含 3 轮):基名剥回「原档」,不堆叠后缀;n 跳过已占用的 1、2 → 3
    async with Session() as s:
        f3 = await fork_story(s, f1.id)
    assert f3.title == "原档(第3拍第3个副本)"
