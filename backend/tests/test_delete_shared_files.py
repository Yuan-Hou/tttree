import json

from sqlalchemy import select

from app.db.models import ImageGen, ReferenceAsset, Story
from app.db.session import create_all, make_engine, make_session_factory
from app.imaging.pipeline import record_generation
from app.state.reducer import reduce_turn
from app.stories.store import create_story, delete_story, fork_story


def _bb() -> str:
    return json.dumps({"story_meta": {"title": "A", "current_scene": "room", "latest_beat": ""},
                       "scenes": {"room": {"name": "房间", "base_prompt": "", "visual_anchors": [],
                                           "state": "", "connections": [], "image_paths": []}},
                       "characters": {}, "items": {}, "notes": []}, ensure_ascii=False)


async def _setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'del.db'}")
    await create_all(engine)
    return make_session_factory(engine)


async def test_delete_respects_shared_files_after_fork(tmp_path):
    Session = await _setup(tmp_path)
    img_rel, ref_rel = "storage/images/a.png", "storage/references/p.png"
    for rel in (img_rel, ref_rel):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"DATA")

    async with Session() as s:
        aid = (await create_story(s, title="A")).id
    async with Session() as s:  # 一轮建 room 场景
        await reduce_turn(story_id=aid, director_b_new_blackboard_str=_bb(), writer_narrative="n",
                          director_a_json="{}", user_input="u", session=s)
    async with Session() as s:
        s.add(ReferenceAsset(story_id=aid, label="立绘", file_path=ref_rel))
        await s.commit()
    async with Session() as s:
        await record_generation(s, story_id=aid, scene_slug="room", kind="new_scene", final_prompt="",
                                ref_asset_ids=[], ref_image_paths=[], output_path=img_rel,
                                origin="user_initiated", source_turn=1)

    # 副本 B(与 A 物理共享文件)
    async with Session() as s:
        bid = (await fork_story(s, aid)).id

    # 删 A:文件仍被 B 引用 → 只删 A 的库记录,保留文件
    async with Session() as s:
        assert await delete_story(s, aid, base_dir=tmp_path) is True
    assert (tmp_path / img_rel).exists() and (tmp_path / ref_rel).exists()  # 文件仍在
    async with Session() as s:
        assert await s.get(Story, aid) is None                              # A 已删
        b_igs = (await s.execute(select(ImageGen).where(ImageGen.story_id == bid))).scalars().all()
    assert len(b_igs) == 1 and b_igs[0].output_path == img_rel
    assert (tmp_path / b_igs[0].output_path).exists()                       # B 的图可访问

    # 删 B:文件此时无人引用 → 物理删除
    async with Session() as s:
        assert await delete_story(s, bid, base_dir=tmp_path) is True
    assert not (tmp_path / img_rel).exists()
    assert not (tmp_path / ref_rel).exists()


async def test_delete_lone_story_still_removes_its_files(tmp_path):
    """无副本时(无其他 story 引用),delete_story 仍照常物理删自己的文件(不回退旧行为)。"""
    Session = await _setup(tmp_path)
    img_rel = "storage/images/solo.png"
    f = tmp_path / img_rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"DATA")
    async with Session() as s:
        sid = (await create_story(s, title="独")).id
        await reduce_turn(story_id=sid, director_b_new_blackboard_str=_bb(), writer_narrative="n",
                          director_a_json="{}", user_input="u", session=s)
    async with Session() as s:
        await record_generation(s, story_id=sid, scene_slug="room", kind="new_scene", final_prompt="",
                                ref_asset_ids=[], ref_image_paths=[], output_path=img_rel,
                                origin="user_initiated", source_turn=1)
    async with Session() as s:
        assert await delete_story(s, sid, base_dir=tmp_path) is True
    assert not (tmp_path / img_rel).exists()  # 无人共享 → 照常物理删
