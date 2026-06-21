"""参考图库 CRUD 的 HTTP 接口(M4.5-E,给 M5 界面用)。每故事独立,按 story_id 过滤。

复用 M3-A 的 app/assets/reference_store 逻辑(增删改查 + 文件存储),这里只包一层 HTTP。
界面留给 M5 React,本文件不含任何界面。
"""

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.assets.reference_store import (
    add_reference,
    delete_reference,
    list_references,
    update_reference,
)
from app.db.models import ReferenceAsset, Story
from app.web.deps import get_session

from app.web.auth_deps import require_story_owner

router = APIRouter(prefix="/story", tags=["references"], dependencies=[Depends(require_story_owner)])


def _ser(a: ReferenceAsset) -> dict:
    return {"asset_id": a.id, "story_id": a.story_id, "label": a.label,
            "description": a.description, "category": a.category, "file_path": a.file_path}


async def _require_story(session: AsyncSession, story_id: str) -> None:
    if await session.get(Story, story_id) is None:
        raise HTTPException(404, "story not found")


async def _owned_asset(session: AsyncSession, story_id: str, asset_id: int) -> ReferenceAsset:
    asset = await session.get(ReferenceAsset, asset_id)
    if asset is None or asset.story_id != story_id:
        raise HTTPException(404, "reference not found in this story")
    return asset


class PatchRefReq(BaseModel):
    label: str | None = None
    description: str | None = None
    category: str | None = None


@router.get("/{story_id}/references")
async def api_list_references(story_id: str, session: AsyncSession = Depends(get_session)) -> list[dict]:
    await _require_story(session, story_id)
    return [_ser(a) for a in await list_references(session, story_id)]


@router.post("/{story_id}/references")
async def api_add_reference(
    story_id: str,
    file: UploadFile = File(...),
    label: str = Form(...),
    description: str = Form(""),
    category: str = Form("其他"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_story(session, story_id)
    suffix = Path(file.filename or "").suffix or ".png"
    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    tmp.write_bytes(await file.read())
    try:
        asset = await add_reference(session, story_id=story_id, label=label,
                                    description=description, category=category, source_file=tmp)
    except ValueError as exc:  # category 非法等
        raise HTTPException(400, str(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)  # add_reference 已 copy 进库,临时文件删掉
    return _ser(asset)


@router.patch("/{story_id}/references/{asset_id}")
async def api_update_reference(
    story_id: str, asset_id: int, req: PatchRefReq, session: AsyncSession = Depends(get_session)
) -> dict:
    await _owned_asset(session, story_id, asset_id)
    try:
        asset = await update_reference(session, asset_id, label=req.label,
                                       description=req.description, category=req.category)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _ser(asset)


@router.delete("/{story_id}/references/{asset_id}")
async def api_delete_reference(
    story_id: str, asset_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    await _owned_asset(session, story_id, asset_id)
    await delete_reference(session, asset_id)
    return {"ok": True, "deleted": asset_id}
