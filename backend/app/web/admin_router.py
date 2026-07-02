"""管理控制台 API(用户系统):管理员列用户 / 建用户 / 改用户名 / 改密码 / 封禁解封。

整个 router 走 require_admin 闸(非管理员 403)。绝不回传口令哈希。用户真相源在 DB,变更经
app.auth.users 双写 DB + 刷进程缓存(本进程即时生效:封禁立刻拦被封用户的下一次请求)。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as users_mod
from app.db.models import NewApiAccount
from app.web.auth_deps import get_current_user, require_admin
from app.web.deps import get_session

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class UserOut(BaseModel):
    id: str
    name: str
    is_admin: bool
    banned: bool
    created_at: datetime | None = None
    # 该用户在 new-api(API 平台)上的子账号登录名;尚未补齐(未首次登录/建号失败)则为 None。
    newapi_username: str | None = None


class CreateUserReq(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class PatchUserReq(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    banned: bool | None = None


class SetPasswordReq(BaseModel):
    new_password: str = Field(min_length=1, max_length=128)


def _out(u: users_mod.User, newapi_username: str | None = None) -> UserOut:
    return UserOut(
        id=u.id, name=u.name, is_admin=u.is_admin, banned=u.banned,
        newapi_username=newapi_username,
    )


@router.get("/users", response_model=list[UserOut])
async def list_users(session: AsyncSession = Depends(get_session)) -> list[UserOut]:
    rows = (await session.execute(select(NewApiAccount))).scalars().all()
    proxy_names = {r.user_id: r.username for r in rows}
    return [_out(u, proxy_names.get(u.id)) for u in users_mod.list_users()]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    req: CreateUserReq, session: AsyncSession = Depends(get_session)
) -> UserOut:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if users_mod.name_exists(name):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = await users_mod.create_user(session, name, req.password)
    return _out(user)


@router.patch("/users/{uid}", response_model=UserOut)
async def patch_user(
    uid: str,
    req: PatchUserReq,
    me: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    target = users_mod.get_user(uid)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    user = target
    if req.name is not None:
        new_name = req.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="用户名不能为空")
        if users_mod.name_exists(new_name, exclude_uid=uid):
            raise HTTPException(status_code=409, detail="用户名已存在")
        user = await users_mod.set_name(session, uid, new_name) or user
    if req.banned is not None:
        if req.banned and uid == me:
            raise HTTPException(status_code=400, detail="不能封禁自己")
        user = await users_mod.set_banned(session, uid, req.banned) or user
    return _out(user)


@router.post("/users/{uid}/password", status_code=204)
async def reset_password(
    uid: str, req: SetPasswordReq, session: AsyncSession = Depends(get_session)
) -> None:
    if users_mod.get_user(uid) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    await users_mod.set_password(session, uid, req.new_password)
