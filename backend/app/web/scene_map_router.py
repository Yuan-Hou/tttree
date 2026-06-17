"""场景地图(静态,第一版)的 HTTP 壳。纯新增**只读**路径:

GET /story/{id}/scene-map —— 从最新黑板 + Turn 表一次性组装三块(节点 / 实线 / 虚线)。
不新增持久字段、不触碰任何写路径(三段式 / 缓存 / 回退重试 / 绘图 / 设置一概不动)。
组装逻辑在 app/stories/scene_map(纯函数),这里只负责取数据。
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blackboard, ImageGen, Story, Turn
from app.imaging.pipeline import CANON_ORIGIN
from app.stories.scene_map import build_scene_map
from app.web.deps import get_session

router = APIRouter(prefix="/story", tags=["scene-map"])


def _load(blob: str | None) -> dict:
    try:
        return json.loads(blob) if blob else {}
    except (ValueError, TypeError):
        return {}


@router.get("/{story_id}/scene-map")
async def get_scene_map(story_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    if await session.get(Story, story_id) is None:
        raise HTTPException(404, "story not found")

    bb_row = await session.get(Blackboard, story_id)
    blackboard = _load(bb_row.json_blob if bb_row else None)

    turn_rows = (
        await session.execute(
            select(Turn).where(Turn.story_id == story_id).order_by(Turn.turn_index)
        )
    ).scalars().all()
    turns = [
        {
            "turn_index": t.turn_index,
            "beat_title": t.beat_title,
            "bb_after": _load(t.blackboard_after),
        }
        for t in turn_rows
    ]

    # 正典图(进黑板那批):给实线标注该轮的有效图、给节点 gallery 标注每图属于哪一拍。
    # 按 id 升序 → build_scene_map 里同 (轮,场景) 取最新有效图、gallery 也按出图先后排列。
    # 带 superseded 标记:被取代图从地图 gallery 过滤(展示职责留给「场景与画」)。
    img_rows = (
        await session.execute(
            select(ImageGen)
            .where(
                ImageGen.story_id == story_id,
                ImageGen.origin == CANON_ORIGIN,
                ImageGen.output_path != "",
            )
            .order_by(ImageGen.id)
        )
    ).scalars().all()
    canon_images = [
        {
            "source_turn": ig.source_turn,
            "scene_slug": ig.scene_slug,
            "output_path": ig.output_path,
            "superseded": ig.superseded,
        }
        for ig in img_rows
    ]

    return build_scene_map(blackboard, turns, canon_images)
