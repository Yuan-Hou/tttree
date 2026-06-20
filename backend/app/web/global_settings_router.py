"""全局设置的 HTTP 壳(全站单例 · 接入点供应商配置)。薄壳,逻辑在 app.global_settings_store。

GET  /global-settings        → 6 个接入点的当前配置(不含明文/密文,只给掩码 + 是否已设 key)
PUT  /global-settings        → 合并更新(body: {endpoints: {endpoint_id: {mode, base_url?, api_key?}}})
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.global_settings_store import (
    GlobalSettingsError,
    get_app_settings,
    public_payload,
    update_app_settings,
)
from app.web.deps import get_session

router = APIRouter(prefix="/global-settings", tags=["global-settings"])


class EndpointChange(BaseModel):
    mode: str  # "site" | "custom"
    base_url: str | None = None
    api_key: str | None = None  # 仅 custom 且要改 key 时传;不回显


class GlobalSettingsReq(BaseModel):
    # endpoint_id → 改动。只含要改的接入点。
    endpoints: dict[str, EndpointChange]


@router.get("")
async def get_global_settings(session: AsyncSession = Depends(get_session)) -> dict:
    row = await get_app_settings(session)
    return public_payload(row)


@router.put("")
async def put_global_settings(
    req: GlobalSettingsReq, session: AsyncSession = Depends(get_session)
) -> dict:
    updates = {eid: change.model_dump() for eid, change in req.endpoints.items()}
    try:
        row = await update_app_settings(session, updates)
    except GlobalSettingsError as exc:
        raise HTTPException(422, str(exc)) from exc
    return public_payload(row)
