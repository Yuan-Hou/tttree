"""故事内设置的 HTTP 壳(子步一:模型设置)。薄壳,逻辑在 stories/settings_store。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Story
from app.imaging.image_models import list_image_model_choices
from app.llm.registry import list_model_choices
from app.stories.settings_store import (
    get_or_create_settings,
    settings_to_dict,
    update_settings,
)
from app.web.deps import get_session

router = APIRouter(prefix="/story", tags=["settings"])


class SettingsReq(BaseModel):
    default_model: str | None = None
    # agent → 模型 id;"" 表示「用全局默认」。只含传入的 agent,未传的不动。
    overrides: dict[str, str] | None = None
    # 绘图模型 id;"" 表示「用全局默认绘图模型」。None = 不动。
    image_model: str | None = None


def _payload(s) -> dict:
    return {
        **settings_to_dict(s),
        "models": list_model_choices(),
        "image_models": list_image_model_choices(),
    }


@router.get("/{story_id}/settings")
async def get_settings(story_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    if await session.get(Story, story_id) is None:
        raise HTTPException(404, "story not found")
    s = await get_or_create_settings(session, story_id)
    return _payload(s)


@router.put("/{story_id}/settings")
async def put_settings(
    story_id: str, req: SettingsReq, session: AsyncSession = Depends(get_session)
) -> dict:
    if await session.get(Story, story_id) is None:
        raise HTTPException(404, "story not found")
    try:
        s = await update_settings(
            session, story_id, default_model=req.default_model, overrides=req.overrides,
            image_model=req.image_model,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _payload(s)
