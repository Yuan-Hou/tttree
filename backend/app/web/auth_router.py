"""鉴权 HTTP 壳:登录(发 token)+ 取当前用户。无注册(用户写死配置文件)。

登录时顺带惰性补齐该用户在 new-api 的账号 + 模型 key,并载入内存(供「本站点服务」用)。
补齐是 best-effort:任何失败都吞掉、不阻断登录(用户照常进站,site 调用此时降级报错)。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import AuthConfigError, make_token
from app.auth.users import authenticate, get_user
from app.config import settings
from app.llm.endpoints import set_user_site_key
from app.newapi.client import NewApiError, get_user_quota
from app.newapi.store import ensure_account, get_account
from app.web.auth_deps import get_current_user
from app.web.deps import get_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _ensure_site_key(session: AsyncSession, uid: str) -> None:
    """登录后:确保该用户有 new-api 模型 key 并载入内存。失败不抛(不阻断登录)。"""
    try:
        acc = await ensure_account(session, uid)
        if acc is not None:
            set_user_site_key(uid, acc.api_key)
    except Exception as exc:  # 兜底:补齐/DB 任何异常都不能让登录失败
        log.warning("登录时补齐 new-api 失败(uid=%s):%s", uid, exc)


class LoginReq(BaseModel):
    name: str = Field(min_length=1)
    password: str


class LoginResp(BaseModel):
    token: str
    uid: str
    name: str


class MeResp(BaseModel):
    uid: str
    name: str


class BalanceResp(BaseModel):
    ready: bool  # 是否已补齐 new-api 账号
    quota: int = 0  # 剩余额度(new-api 原始单位)
    used_quota: int = 0  # 已用额度
    balance_usd: float = 0.0  # 剩余额度折算美元(quota / new_api_quota_per_unit)
    error: str | None = None  # 取额度失败的原因(站点宕机等),此时上面数值不可信


@router.post("/login", response_model=LoginResp)
async def login(req: LoginReq, session: AsyncSession = Depends(get_session)) -> LoginResp:
    user = authenticate(req.name, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或口令错误")
    try:
        token = make_token(user.id)
    except AuthConfigError:
        raise HTTPException(status_code=503, detail="服务未配置 APP_SECRET")
    await _ensure_site_key(session, user.id)  # 惰性补齐 new-api(失败不阻断)
    return LoginResp(token=token, uid=user.id, name=user.name)


@router.get("/me", response_model=MeResp)
async def me(uid: str = Depends(get_current_user)) -> MeResp:
    user = get_user(uid)  # 依赖已确保 uid 在清单内
    return MeResp(uid=uid, name=user.name if user else uid)


@router.get("/balance", response_model=BalanceResp)
async def balance(
    uid: str = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> BalanceResp:
    """当前用户在 new-api 对应账户的余额(经管理员查其额度)。未补齐 / 取数失败时 ready/error 给出说明。"""
    acc = await get_account(session, uid)
    if acc is None:
        return BalanceResp(ready=False, error="new-api 账号尚未补齐(重新登录可自动补齐)")
    try:
        q = await get_user_quota(acc.newapi_user_id)
    except NewApiError as exc:
        return BalanceResp(ready=True, error=str(exc))
    per = settings.new_api_quota_per_unit or 500000.0
    return BalanceResp(
        ready=True,
        quota=q["quota"],
        used_quota=q["used_quota"],
        balance_usd=round(q["quota"] / per, 2),
    )
