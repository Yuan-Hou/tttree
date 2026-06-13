"""参考图库 CRUD(纯逻辑,被 CLI 包装)。文件落 storage/references/,
入库 file_path 存「相对 base_dir」的相对路径。"""

import re
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ReferenceAsset
from app.storage import BACKEND_ROOT, REFERENCES_SUBDIR

VALID_CATEGORIES = ("角色", "物品", "场景氛围", "其他")
_SAFE = re.compile(r"[^0-9A-Za-z一-鿿]+")


def _safe_stem(label: str) -> str:
    s = _SAFE.sub("_", label).strip("_")
    return s or "ref"


async def add_reference(
    session: AsyncSession,
    *,
    story_id: str,
    label: str,
    description: str,
    category: str,
    source_file: Path,
    base_dir: Path = BACKEND_ROOT,
) -> ReferenceAsset:
    source_file = Path(source_file)
    if not source_file.is_file():
        raise FileNotFoundError(f"源文件不存在: {source_file}")
    if category not in VALID_CATEGORIES:
        raise ValueError(f"category 必须是 {VALID_CATEGORIES} 之一,实得 {category!r}")

    dest_dir = base_dir / REFERENCES_SUBDIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{_safe_stem(label)}_{uuid.uuid4().hex[:8]}{source_file.suffix.lower()}"
    shutil.copy2(source_file, dest_dir / fname)
    rel_path = f"{REFERENCES_SUBDIR}/{fname}"

    asset = ReferenceAsset(
        story_id=story_id,
        label=label,
        description=description,
        category=category,
        file_path=rel_path,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


async def list_references(session: AsyncSession, story_id: str) -> list[ReferenceAsset]:
    rows = await session.execute(
        select(ReferenceAsset).where(ReferenceAsset.story_id == story_id).order_by(ReferenceAsset.id)
    )
    return list(rows.scalars().all())


async def update_reference_description(
    session: AsyncSession, asset_id: int, new_description: str
) -> ReferenceAsset:
    asset = await session.get(ReferenceAsset, asset_id)
    if asset is None:
        raise KeyError(f"参考图 id={asset_id} 不存在")
    asset.description = new_description
    await session.commit()
    await session.refresh(asset)
    return asset


async def delete_reference(
    session: AsyncSession, asset_id: int, *, base_dir: Path = BACKEND_ROOT
) -> bool:
    asset = await session.get(ReferenceAsset, asset_id)
    if asset is None:
        return False
    file_abs = base_dir / asset.file_path
    file_abs.unlink(missing_ok=True)
    await session.delete(asset)
    await session.commit()
    return True
