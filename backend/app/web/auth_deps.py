"""鉴权依赖:从 Authorization: Bearer <token> 解析当前用户号。

校验通过 → 把 uid 写进 current_uid ContextVar(供深处凭证解析取用)并返回。
缺/坏 token 或用户已不在清单 → 401;APP_SECRET 未配置 → 503(服务端配置问题,非用户错)。
对话前端硬要求登录:无匿名兜底。
"""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import current_uid
from app.auth.tokens import AuthConfigError, decode_uid
from app.auth.users import get_user
from app.db.models import Story
from app.web.deps import get_session


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None


async def get_current_user(authorization: str | None = Header(default=None)) -> str:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        uid = decode_uid(token)
    except AuthConfigError:
        raise HTTPException(status_code=503, detail="服务未配置 APP_SECRET")
    if not uid or get_user(uid) is None:
        raise HTTPException(status_code=401, detail="登录已失效,请重新登录")
    current_uid.set(uid)
    return uid


async def require_story_owner(
    story_id: str,
    uid: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> str:
    """故事级路由的归属闸:校验 {story_id} 属于当前用户,否则 404(不泄露存在性)。

    用作 prefix='/story' 各路由的 router 级依赖(它们都带 {story_id} 路径段)。校验通过即放行,
    路由函数照旧自取 story_id;同时 get_current_user 已把 uid 写进 contextvar 供深处凭证解析用。
    """
    story = await session.get(Story, story_id)
    if story is None or story.owner_id != uid:
        raise HTTPException(status_code=404, detail="story not found")
    return story_id
