import json
from pathlib import Path

from sqlalchemy import select

from app.assets.reference_store import (
    add_reference,
    delete_reference,
    list_references,
    update_reference_description,
)
from app.db.models import ImageGen, ReferenceAsset
from app.db.session import create_all, make_engine, make_session_factory
from app.storage import REFERENCES_SUBDIR

STORY = "story-ref"

# 一个最小合法 PNG(1x1)的字节,用作占位测试图
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


async def _setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'refs.db'}")
    await create_all(engine)
    return engine, make_session_factory(engine)


def _placeholder(tmp_path) -> Path:
    p = tmp_path / "placeholder.png"
    p.write_bytes(PNG_1x1)
    return p


async def test_reference_asset_crud(tmp_path):
    engine, Session = await _setup(tmp_path)
    src = _placeholder(tmp_path)

    # 加图
    async with Session() as s:
        a = await add_reference(
            s,
            story_id=STORY,
            label="主角立绘",
            description="主角的正式立绘,用于锚定外貌与服饰的跨图一致。",
            category="角色",
            source_file=src,
            base_dir=tmp_path,
        )
    assert a.id is not None
    assert a.file_path.startswith(REFERENCES_SUBDIR + "/")
    copied = tmp_path / a.file_path
    assert copied.is_file() and copied.read_bytes() == PNG_1x1  # 文件真被复制进库

    # 列出
    async with Session() as s:
        assets = await list_references(s, STORY)
    assert len(assets) == 1
    assert assets[0].label == "主角立绘"
    assert assets[0].category == "角色"
    assert "跨图一致" in assets[0].description  # 中文 value 完整

    # 改说明
    async with Session() as s:
        a2 = await update_reference_description(s, assets[0].id, "更新后的说明:正脸特写参考。")
    assert a2.description == "更新后的说明:正脸特写参考。"

    # 删图(行 + 文件都删)
    async with Session() as s:
        ok = await delete_reference(s, a2.id, base_dir=tmp_path)
        assert ok is True
        remaining = await list_references(s, STORY)
    assert remaining == []
    assert not copied.exists()

    await engine.dispose()
    print("\n[references] add/list/edit/delete 全程正确;文件复制与删除一致。")


async def test_imagegen_roundtrip(tmp_path):
    engine, Session = await _setup(tmp_path)
    async with Session() as s:
        s.add(
            ImageGen(
                story_id=STORY,
                scene_slug="forest_edge",
                kind="new_scene",
                final_prompt="黄昏的森林空地,古树环绕,金色夕照斜切落叶。",
                ref_asset_ids=json.dumps([1, 2]),
                ref_image_paths=json.dumps(["storage/images/forest_edge_001.png"]),
                output_path="storage/images/forest_edge_002.png",
                origin="director_b_proposal",
                source_turn=3,
            )
        )
        await s.commit()

    async with Session() as s:
        row = (await s.execute(select(ImageGen).where(ImageGen.story_id == STORY))).scalar_one()
    assert row.kind == "new_scene"
    assert json.loads(row.ref_asset_ids) == [1, 2]
    assert json.loads(row.ref_image_paths) == ["storage/images/forest_edge_001.png"]
    assert row.origin == "director_b_proposal"
    assert row.source_turn == 3
    assert "金色夕照" in row.final_prompt  # 中文 value 往返无损
    assert row.created_at is not None

    await engine.dispose()
    print("\n[imagegen] ImageGen 建表 + 增查往返无损(JSON 字段与中文 value 正确)。")
