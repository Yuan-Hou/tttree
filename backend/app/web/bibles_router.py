"""故事自定义圣经(文风 / 画风)的 HTTP 壳(故事内设置 · bible 子步)。

每故事各一篇自由文本,空串 = 用全局打包默认。整存整取:GET 取「当前自定义值 + 全局默认 + 可选模板」,
PUT 整篇覆盖。随 fork 复制、随 delete 清理(逻辑都在 StorySettings 上,见 stories/store)。

模板在进程启动时扫描一次(app.agents.bibles),此处只读已扫好的常驻列表。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.bibles import (
    DEFAULT_STYLE_BIBLE,
    DEFAULT_VISUAL_STYLE_BIBLE,
    STYLE_TEMPLATES,
    VISUAL_TEMPLATES,
)
from app.db.models import Story
from app.stories.settings_store import get_or_create_settings, update_bibles
from app.web.deps import get_session

from app.web.auth_deps import require_story_owner

router = APIRouter(prefix="/story", tags=["bibles"], dependencies=[Depends(require_story_owner)])


class BiblesReq(BaseModel):
    # 只含传入的字段才更新;空串 = 清空该故事自定义 → 回退全局默认。
    style_bible: str | None = None
    visual_style_bible: str | None = None


async def _require_story(session: AsyncSession, story_id: str) -> None:
    if await session.get(Story, story_id) is None:
        raise HTTPException(404, "story not found")


def _payload(s) -> dict:
    """整面板一次取齐:各圣经的当前自定义值、全局默认、可选模板(名+正文)。"""
    return {
        "style": {
            "custom": s.style_bible or "",
            "default": DEFAULT_STYLE_BIBLE,
            "templates": STYLE_TEMPLATES,
        },
        "visual": {
            "custom": s.visual_style_bible or "",
            "default": DEFAULT_VISUAL_STYLE_BIBLE,
            "templates": VISUAL_TEMPLATES,
        },
    }


@router.get("/{story_id}/bibles")
async def get_bibles(story_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    await _require_story(session, story_id)
    s = await get_or_create_settings(session, story_id)
    return _payload(s)


@router.put("/{story_id}/bibles")
async def put_bibles(
    story_id: str, req: BiblesReq, session: AsyncSession = Depends(get_session)
) -> dict:
    await _require_story(session, story_id)
    s = await update_bibles(
        session, story_id, style_bible=req.style_bible, visual_style_bible=req.visual_style_bible
    )
    return _payload(s)
