import json

from sqlalchemy import select

from app.assets.reference_store import add_reference, list_references
from app.db.models import Blackboard, ImageGen, Story, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.storage import IMAGES_SUBDIR
from app.stories.store import create_story, delete_story, list_stories, rename_story

PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


async def _setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'stories.db'}")
    await create_all(engine)
    return engine, make_session_factory(engine)


def _placeholder(tmp_path, name="ref.png"):
    p = tmp_path / name
    p.write_bytes(PNG_1x1)
    return p


async def test_story_isolation_and_delete_cleanup(tmp_path):
    engine, Session = await _setup(tmp_path)

    async with Session() as s:
        a = await create_story(s, title="故事A")
        b = await create_story(s, title="故事B")
        aid, bid = a.id, b.id

    # 两个故事的初始黑板独立
    async with Session() as s:
        bb_a = json.loads((await s.get(Blackboard, aid)).json_blob)
        bb_b = json.loads((await s.get(Blackboard, bid)).json_blob)
    # 标题只是档案标记,不进黑板(不参与故事、不喂 agent);只在 Story.title 行上
    assert "title" not in bb_a["story_meta"] and "title" not in bb_b["story_meta"]
    async with Session() as s:
        assert (await s.get(Story, aid)).title == "故事A" and (await s.get(Story, bid)).title == "故事B"

    # 参考图每故事独立:A 登记的图在 B 里查不到
    src = _placeholder(tmp_path)
    async with Session() as s:
        ra = await add_reference(s, story_id=aid, label="A的立绘", description="仅属于A", category="角色", source_file=src, base_dir=tmp_path)
        await add_reference(s, story_id=bid, label="B的立绘", description="仅属于B", category="角色", source_file=src, base_dir=tmp_path)
    async with Session() as s:
        a_refs = await list_references(s, aid)
        b_refs = await list_references(s, bid)
    assert [r.label for r in a_refs] == ["A的立绘"]
    assert [r.label for r in b_refs] == ["B的立绘"]
    ref_a_file = tmp_path / ra.file_path
    assert ref_a_file.is_file()

    # 给 A 写 Turn + 一张图(磁盘文件 + ImageGen + 黑板 image_paths)
    img_rel = f"{IMAGES_SUBDIR}/a_scene_0001.png"
    (tmp_path / img_rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / img_rel).write_bytes(PNG_1x1)
    async with Session() as s:
        s.add(Turn(story_id=aid, turn_index=1, beat_title="A的第一拍", narrative="A 的叙事"))
        s.add(ImageGen(story_id=aid, scene_slug="a_scene", kind="new_scene", output_path=img_rel, origin="user_initiated"))
        bb_row = await s.get(Blackboard, aid)
        bb = json.loads(bb_row.json_blob)
        bb["scenes"]["a_scene"] = {"name": "A场景", "image_paths": [img_rel]}
        bb_row.json_blob = json.dumps(bb, ensure_ascii=False)
        await s.commit()

    # 列表带 turn_count
    async with Session() as s:
        infos = {i.id: i for i in await list_stories(s, "1")}
    assert infos[aid].turn_count == 1
    assert infos[bid].turn_count == 0

    # 重命名 A:只改 Story.title 行;黑板不被触碰(标题不参与故事)
    async with Session() as s:
        await rename_story(s, aid, "故事A改名")
        assert (await s.get(Story, aid)).title == "故事A改名"
        assert "title" not in json.loads((await s.get(Blackboard, aid)).json_blob)["story_meta"]

    # 删除 A:行 + 磁盘文件清理;B 不受影响
    async with Session() as s:
        ok = await delete_story(s, aid, base_dir=tmp_path)
    assert ok is True
    assert not ref_a_file.exists()  # A 的参考图文件已删
    assert not (tmp_path / img_rel).exists()  # A 的生成图文件已删

    async with Session() as s:
        assert await s.get(Story, aid) is None
        assert await s.get(Blackboard, aid) is None
        assert (await s.execute(select(Turn).where(Turn.story_id == aid))).first() is None
        assert (await s.execute(select(ImageGen).where(ImageGen.story_id == aid))).first() is None
        assert await list_references(s, aid) == []
        # B 完好
        assert await s.get(Story, bid) is not None
        assert [r.label for r in await list_references(s, bid)] == ["B的立绘"]
    await engine.dispose()
    print("\n[stories] 双故事隔离正确;删除 A 连带清理行+磁盘文件;B 不受影响。")
