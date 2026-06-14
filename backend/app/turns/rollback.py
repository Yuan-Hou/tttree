"""回退(rollback)——时间倒流操作之一(M4.5-C-1)。

回退最新一轮:恢复黑板到上一轮的 blackboard_after、删除最新 Turn 记录。按核心规则,
被回退轮的 Director-B 诞生的场景随之消失——这在数据上由「黑板回滚到上一轮快照」自然达成
(上一轮快照里本就不含这些后来才诞生的场景);其图在黑板里的引用随场景一起消失,但
**ImageGen 记录与磁盘文件始终保留**(图是花钱生成的真实资产,未来全局图库收纳;消失的只是
黑板/对话里的引用,不是资产本身)——本函数完全不触碰 ImageGen 与磁盘文件即满足此点。

可连续回退。回退到的那一轮成为新「最新一轮」,系统处于干净、可继续状态:对它的后续推进/
重试与「从未回退过、直接操作那一轮」完全一致(reducer 的 next_idx = max(turn_index)+1
会自然复用被腾出的轮号),不留任何特殊/中间状态。
"""

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blackboard, Turn
from app.stories.store import empty_blackboard
from app.turns.scene_origins import images_for_scenes, scenes_born_in_turn


@dataclass
class RollbackResult:
    ok: bool
    rolled_back_turn: int | None = None       # 被作废的轮
    new_latest_turn: int | None = None        # 回退后的新「最新一轮」(回退首轮后为 None)
    released_scene_slugs: list[str] = field(default_factory=list)   # 因诞生轮被作废而消失的场景
    released_image_paths: list[str] = field(default_factory=list)   # 这些场景名下被解除引用的图(资产仍保留)
    blackboard: dict = field(default_factory=dict)                  # 回退后的权威黑板
    reason: str | None = None


async def rollback_latest_turn(session: AsyncSession, story_id: str) -> RollbackResult:
    turns = (
        await session.execute(
            select(Turn).where(Turn.story_id == story_id).order_by(Turn.turn_index.desc())
        )
    ).scalars().all()
    if not turns:
        return RollbackResult(ok=False, reason="没有可回退的回合")
    latest = turns[0]
    n = latest.turn_index

    # 回退前记录「诞生于本轮、将随之消失」的场景及其图(用于报告/验证;图资产不动)。
    # 判存续依据 scene.origin_turn,不是 ImageGen.source_turn。
    bb_row = await session.get(Blackboard, story_id)
    cur_bb = json.loads(bb_row.json_blob) if bb_row and bb_row.json_blob else {}
    released_slugs = scenes_born_in_turn(cur_bb, n)
    released_images = await images_for_scenes(session, story_id, released_slugs)
    released_paths = [ig.output_path for ig in released_images if ig.output_path]

    # 目标黑板 = 上一轮 blackboard_after;若回退的是首轮(无上一轮)→ 恢复到初始空黑板。
    prev = turns[1] if len(turns) > 1 else None
    if prev is not None:
        target_bb_str = prev.blackboard_after
    else:
        title = (cur_bb.get("story_meta") or {}).get("title", "")
        target_bb_str = json.dumps(empty_blackboard(title), ensure_ascii=False)
    target_bb = json.loads(target_bb_str)

    # 恢复黑板 + 删除最新 Turn。ImageGen 记录与磁盘文件均不触碰 → 图资产保留。
    if bb_row is None:
        session.add(Blackboard(story_id=story_id, json_blob=target_bb_str))
    else:
        bb_row.json_blob = target_bb_str
    await session.delete(latest)
    await session.commit()

    return RollbackResult(
        ok=True,
        rolled_back_turn=n,
        new_latest_turn=prev.turn_index if prev else None,
        released_scene_slugs=released_slugs,
        released_image_paths=released_paths,
        blackboard=target_bb,
    )
