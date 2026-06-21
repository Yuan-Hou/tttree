from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.stories.store import (
    StoryInfo,
    create_story,
    delete_story,
    fork_story,
    list_stories,
    rename_story,
)
from app.web.auth_deps import get_current_user, require_story_owner
from app.web.deps import get_session

router = APIRouter(prefix="/stories", tags=["stories"])


class CreateStoryReq(BaseModel):
    title: str = Field(min_length=1)


class RenameStoryReq(BaseModel):
    title: str = Field(min_length=1)


class StoryResp(BaseModel):
    id: str
    title: str
    created_at: str
    last_active_at: str
    turn_count: int

    @classmethod
    def of(cls, info: StoryInfo) -> "StoryResp":
        return cls(**info.__dict__)


@router.post("", response_model=StoryResp)
async def api_create_story(
    req: CreateStoryReq,
    uid: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StoryResp:
    story = await create_story(session, title=req.title, owner_id=uid)
    return StoryResp(
        id=story.id,
        title=story.title,
        created_at=story.created_at.isoformat(),
        last_active_at=story.last_active_at.isoformat(),
        turn_count=0,
    )


@router.get("", response_model=list[StoryResp])
async def api_list_stories(
    uid: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[StoryResp]:
    return [StoryResp.of(i) for i in await list_stories(session, uid)]


@router.patch("/{story_id}", response_model=StoryResp, dependencies=[Depends(require_story_owner)])
async def api_rename_story(
    story_id: str,
    req: RenameStoryReq,
    uid: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StoryResp:
    story = await rename_story(session, story_id, req.title)
    if story is None:
        raise HTTPException(404, "story not found")
    infos = {i.id: i for i in await list_stories(session, uid)}
    return StoryResp.of(infos[story_id])


@router.delete("/{story_id}", dependencies=[Depends(require_story_owner)])
async def api_delete_story(story_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    ok = await delete_story(session, story_id)
    if not ok:
        raise HTTPException(404, "story not found")
    return {"ok": True, "deleted": story_id}


@router.post("/{story_id}/fork", response_model=StoryResp, dependencies=[Depends(require_story_owner)])
async def api_fork_story(
    story_id: str,
    uid: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StoryResp:
    """副本(后悔药):完整克隆故事档案,图片文件物理共享(M4.5-C-2)。新副本出现在书架。"""
    story = await fork_story(session, story_id)
    if story is None:
        raise HTTPException(404, "story not found")
    infos = {i.id: i for i in await list_stories(session, uid)}
    return StoryResp.of(infos[story.id])
