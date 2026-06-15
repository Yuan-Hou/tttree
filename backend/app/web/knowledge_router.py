"""知识库(设定圣经)读写的 HTTP 壳。薄壳,逻辑在 app/knowledge/store(M4.5-A)。

知识库是每故事一大篇自由文本(角色人设/世界观/关系等,用户自己组织)。整存整取:
GET 取整篇、PUT 整篇覆盖。随 fork 复制、随 delete 清理(见 stories/store)。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Story
from app.knowledge.store import get_knowledge, set_knowledge
from app.web.deps import get_session

router = APIRouter(prefix="/story", tags=["knowledge"])


class KnowledgeReq(BaseModel):
    content: str  # 整篇覆盖(可为空串 = 清空内容)


async def _require_story(session: AsyncSession, story_id: str) -> None:
    if await session.get(Story, story_id) is None:
        raise HTTPException(404, "story not found")


@router.get("/{story_id}/knowledge")
async def api_get_knowledge(story_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    await _require_story(session, story_id)
    return {"content": await get_knowledge(session, story_id)}


@router.put("/{story_id}/knowledge")
async def api_set_knowledge(
    story_id: str, req: KnowledgeReq, session: AsyncSession = Depends(get_session)
) -> dict:
    await _require_story(session, story_id)
    row = await set_knowledge(session, story_id, req.content)
    return {"content": row.content}
