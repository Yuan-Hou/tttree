"""出图后的落库:把新图写进黑板 image_paths 简表,并写一条 ImageGen 完整记录。
M3 的 CLI 与 M4 的前端接口都复用此函数。"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blackboard, ImageGen


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
