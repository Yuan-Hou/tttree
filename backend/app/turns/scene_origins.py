"""场景诞生点(origin_turn)的反查工具(M4.5-C-0)。

时间倒流语义的地基:每个场景在黑板里记录 origin_turn(诞生于第几轮的 Director-B,
由 reducer 权威打点,见 app/state/reducer.py:_stamp_scene_origins)。图通过 scene_slug
间接关联到场景的诞生点,自己不记轮次。

回退/重试判断「某轮诞生了哪些场景、这些场景名下有哪些图」时用本模块反查。
判存续依据 scene.origin_turn,**不是** ImageGen.source_turn(后者只是审计『图实际在哪轮生成』)。
"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blackboard, ImageGen


def scenes_born_in_turn(blackboard: dict, turn_index: int) -> list[str]:
    """黑板里 origin_turn == turn_index 的场景 slug(即「诞生于该轮」的场景)。"""
    return [
        slug
        for slug, sc in (blackboard.get("scenes") or {}).items()
        if isinstance(sc, dict) and sc.get("origin_turn") == turn_index
    ]


async def images_for_scenes(
    session: AsyncSession, story_id: str, scene_slugs: list[str]
) -> list[ImageGen]:
    """这些场景名下的所有 ImageGen 记录(含 new_scene/variant/reuse 各类)。"""
    if not scene_slugs:
        return []
    rows = (
        await session.execute(
            select(ImageGen)
            .where(ImageGen.story_id == story_id, ImageGen.scene_slug.in_(scene_slugs))
            .order_by(ImageGen.id)
        )
    ).scalars().all()
    return list(rows)


async def born_in_turn(session: AsyncSession, story_id: str, turn_index: int) -> dict:
    """反查某轮诞生的场景及其图(回退/重试要用)。依据**当前黑板**的 origin_turn。

    返回 {"scene_slugs": [...], "images": [ImageGen, ...]}。
    """
    bb_row = await session.get(Blackboard, story_id)
    bb = json.loads(bb_row.json_blob) if bb_row else {}
    slugs = scenes_born_in_turn(bb, turn_index)
    images = await images_for_scenes(session, story_id, slugs)
    return {"scene_slugs": slugs, "images": images}
