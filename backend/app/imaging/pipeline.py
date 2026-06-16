"""出图后的落库:把新图写进黑板 image_paths 简表,并写一条 ImageGen 完整记录。
M3 的 CLI 与 M4 的前端接口都复用此函数。"""

import json

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blackboard, ImageGen

# 绘图归属:ImageGen.origin 决定待遇。
#   director_b_proposal = 故事正典:进黑板 image_paths、进绘图 Agent 候选池、参与连贯参考。
#   user_initiated      = 用户私人草稿:不进黑板、对 Agent 隐身;仅用户可见、可手动引用。
# 一律按 origin 字段判定,不靠其他启发式。
CANON_ORIGIN = "director_b_proposal"
DRAFT_ORIGIN = "user_initiated"


def is_canon_origin(origin: str) -> bool:
    """该来源的图是否为故事正典(进黑板 + 进 Agent 候选池)。"""
    return origin == CANON_ORIGIN


async def record_generation(
    session: AsyncSession,
    *,
    story_id: str,
    scene_slug: str,
    kind: str,
    final_prompt: str,
    ref_asset_ids: list[int],
    ref_image_paths: list[str],
    output_path: str,
    origin: str,
    source_turn: int | None = None,
    append_to_blackboard: bool = True,
) -> ImageGen:
    # 取代(以「同场景同轮次」为单位):新出一张正典图前,把该 (scene, source_turn) 下旧的正典图
    # 自动标记 superseded —— 组内只留最新一张有效。被取代的图退出 Agent 候选池,但仍在 image_paths、
    # 仍可 RefPicker 手动选。只在「新追加进黑板的正典图」时触发(reuse 不新增、手动草稿不进池,均不触发)。
    # 不分 new_scene/variant:同一轮内某场景的 kind 恒定(诞生轮才 new_scene,之后皆 variant),
    # 故按 kind 再分组是冗余;「该场景这一轮的有效图」天然以 (scene, turn) 为单位。
    if append_to_blackboard and is_canon_origin(origin) and source_turn is not None:
        await session.execute(
            update(ImageGen)
            .where(
                ImageGen.story_id == story_id,
                ImageGen.scene_slug == scene_slug,
                ImageGen.source_turn == source_turn,
                ImageGen.origin == CANON_ORIGIN,
                ImageGen.superseded.is_(False),
            )
            .values(superseded=True)
        )

    # 黑板 image_paths:存「该场景当前有哪些图」简表(append 新图)。
    # reuse 复用已在库中的图,不重复追加(append_to_blackboard=False)。
    row = await session.get(Blackboard, story_id)
    if append_to_blackboard and row is not None:
        bb = json.loads(row.json_blob)
        scene = (bb.get("scenes") or {}).get(scene_slug)
        if scene is not None:
            scene.setdefault("image_paths", []).append(output_path)
            row.json_blob = json.dumps(bb, ensure_ascii=False)

    # ImageGen:每次出图的完整出处
    ig = ImageGen(
        story_id=story_id,
        scene_slug=scene_slug,
        kind=kind,
        final_prompt=final_prompt,
        ref_asset_ids=json.dumps(ref_asset_ids, ensure_ascii=False),
        ref_image_paths=json.dumps(ref_image_paths, ensure_ascii=False),
        output_path=output_path,
        origin=origin,
        source_turn=source_turn,
    )
    session.add(ig)
    await session.commit()
    await session.refresh(ig)
    return ig
