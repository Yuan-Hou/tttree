"""new-api 账号的读写 + 惰性补齐(每 vore-tree 用户一行,见 db.models.NewApiAccount)。

ensure_account:有就返回、没有就建。建号失败(NewApiError)吞掉并返回 None —— 上层(登录钩子)据此
「登录照常成功、site 调用降级」。per-uid 锁串行化,避免同一用户并发登录建出两个 new-api 子用户。
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NewApiAccount
from app.llm.endpoints import clear_all_site_keys, set_user_site_key
from app.newapi.client import NewApiError, is_provisioning_configured, provision

log = logging.getLogger(__name__)

_locks: dict[str, asyncio.Lock] = {}


def _lock_for(uid: str) -> asyncio.Lock:
    lk = _locks.get(uid)
    if lk is None:
        lk = _locks[uid] = asyncio.Lock()
    return lk


async def get_account(session: AsyncSession, uid: str) -> NewApiAccount | None:
    return await session.get(NewApiAccount, uid)


async def provision_and_store(session: AsyncSession, uid: str) -> NewApiAccount:
    """跑完整建号流程并落库(失败抛 NewApiError)。调用前应确认该用户尚无账号。"""
    acc = await provision(uid)
    row = NewApiAccount(
        user_id=uid,
        newapi_user_id=acc.newapi_user_id,
        username=acc.username,
        password=acc.password,
        token_id=acc.token_id,
        api_key=acc.api_key,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def load_site_keys_into_memory(session: AsyncSession) -> None:
    """启动时调用:把库里所有用户的 new-api 模型 key 逐用户载入内存(供 resolve_endpoint 的本站点服务用)。"""
    clear_all_site_keys()
    rows = (await session.execute(select(NewApiAccount))).scalars().all()
    for r in rows:
        set_user_site_key(r.user_id, r.api_key)


async def ensure_account(session: AsyncSession, uid: str) -> NewApiAccount | None:
    """有就返回;没有且 new-api 已配置则补齐。失败 → 记日志返回 None,绝不抛(不阻断登录)。"""
    existing = await get_account(session, uid)
    if existing is not None:
        return existing
    if not is_provisioning_configured():
        return None
    async with _lock_for(uid):
        existing = await get_account(session, uid)  # 锁内复查,防并发重复建号
        if existing is not None:
            return existing
        try:
            return await provision_and_store(session, uid)
        except NewApiError as exc:
            log.warning("new-api 为用户 %s 补齐失败:%s", uid, exc)
            return None
