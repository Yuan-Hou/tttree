"""故事迁移包(parquet .zip)导出/导入往返:跨账号完整重建 + 跨表 ID 重映射 + 图片字节落盘。"""

import json
import zipfile
from io import BytesIO

import pytest
from sqlalchemy import select

from app.db.models import (
    Blackboard,
    DrawProposal,
    ImageGen,
    Knowledge,
    ReferenceAsset,
    Story,
    StorySettings,
    Turn,
)
from app.db.session import create_all, make_engine, make_session_factory
from app.stories.migrate import export_bundle, import_bundle
from app.stories.store import create_story


@pytest.fixture
async def env(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    src_base = tmp_path / "src"  # 源实例 storage 根
    dst_base = tmp_path / "dst"  # 目标实例 storage 根(另一个部署)
    yield Session, src_base, dst_base
    await engine.dispose()


def _write(base, rel, data: bytes):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


async def _seed(Session, base) -> str:
    """建一卷有图、有参考图、有绘图待办的完整故事(owner=1),返回 story_id。"""
    async with Session() as s:
        sid = (await create_story(s, title="迁移源", owner_id="1")).id
        (await s.get(Blackboard, sid)).json_blob = json.dumps({"scenes": {}})  # create_story 已建该行
        s.add(Knowledge(story_id=sid, content="设定底座"))
        s.add(StorySettings(story_id=sid, default_model="deepseek-v4-pro", writer_model="x"))
        s.add(Turn(story_id=sid, turn_index=1, beat_title="拍1", user_input="行动", narrative="第一段成稿"))
        ref = ReferenceAsset(story_id=sid, label="主角", description="d",
                             category="角色", file_path="storage/references/r1.png")
        s.add(ref)
        await s.flush()
        ig = ImageGen(story_id=sid, scene_slug="room", kind="new_scene", final_prompt="p",
                      ref_asset_ids=json.dumps([ref.id]), output_path="storage/images/g1.png",
                      source_turn=1)
        s.add(ig)
        await s.flush()
        s.add(DrawProposal(story_id=sid, scene_slug="room", origin_proposal_turn=1, kind="new_scene",
                           status="done", done_image_id=ig.id,
                           draft_manifest=json.dumps([{"source": "reference_asset", "asset_id": ref.id}])))
        await s.commit()
    _write(base, "storage/references/r1.png", b"REF-BYTES")
    _write(base, "storage/images/g1.png", b"IMG-BYTES")
    return sid


async def test_bundle_is_zip_of_parquet_with_blobs(env):
    Session, src_base, _ = env
    sid = await _seed(Session, src_base)
    async with Session() as s:
        data, filename = await export_bundle(s, sid, base_dir=src_base)

    assert filename.endswith(".vtree.zip")
    with zipfile.ZipFile(BytesIO(data)) as z:
        names = set(z.namelist())
        assert "manifest.json" in names and "blobs.parquet" in names
        assert "tables/turns.parquet" in names and "tables/image_gens.parquet" in names
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["kind"] == "vore-tree-story-bundle" and manifest["version"] == 1
        assert manifest["tables"]["turns"] == 1 and manifest["blob_count"] == 2


async def test_export_missing_story_raises(env):
    Session, src_base, _ = env
    async with Session() as s:
        with pytest.raises(KeyError):
            await export_bundle(s, "nope", base_dir=src_base)


async def test_roundtrip_remaps_ids_and_copies_blobs(env):
    Session, src_base, dst_base = env
    sid = await _seed(Session, src_base)
    async with Session() as s:
        data, _ = await export_bundle(s, sid, base_dir=src_base)

    # 导入到另一账号(owner=2),目标实例另一个 storage 根
    async with Session() as s:
        new_story = await import_bundle(s, "2", data, base_dir=dst_base)
    nsid = new_story.id

    assert nsid != sid
    assert new_story.owner_id == "2"      # 归属改为导入者
    assert new_story.title == "迁移源"     # 标题原样

    async with Session() as s:
        # 子表都按新 story_id 重建
        turn = (await s.execute(select(Turn).where(Turn.story_id == nsid))).scalar_one()
        assert turn.narrative == "第一段成稿" and turn.turn_index == 1
        assert (await s.get(Knowledge, nsid)).content == "设定底座"
        assert (await s.get(StorySettings, nsid)).writer_model == "x"

        ref = (await s.execute(select(ReferenceAsset).where(ReferenceAsset.story_id == nsid))).scalar_one()
        ig = (await s.execute(select(ImageGen).where(ImageGen.story_id == nsid))).scalar_one()
        dp = (await s.execute(select(DrawProposal).where(DrawProposal.story_id == nsid))).scalar_one()

        # ref_asset_ids 重映射到新参考图 id(不再是源库的旧 id)
        assert json.loads(ig.ref_asset_ids) == [ref.id]
        # done_image_id 重映射到新 ImageGen id
        assert dp.done_image_id == ig.id
        # draft_manifest 里的 asset_id 也重映射
        assert json.loads(dp.draft_manifest)[0]["asset_id"] == ref.id
        # 图片相对路径原样保留(跨实例文件名不撞)
        assert ig.output_path == "storage/images/g1.png"

    # 图片二进制已落回目标实例磁盘,内容一致
    assert (dst_base / "storage/images/g1.png").read_bytes() == b"IMG-BYTES"
    assert (dst_base / "storage/references/r1.png").read_bytes() == b"REF-BYTES"


async def test_import_rejects_garbage(env):
    Session, _, dst_base = env
    async with Session() as s:
        with pytest.raises(ValueError):
            await import_bundle(s, "2", b"not a zip", base_dir=dst_base)
