"""reducer:纯逻辑状态归并。不调 LLM、不理解语义。

职责:解析 Director-B 的新黑板 → 跑非阻断告警检查 → 覆盖写 Blackboard → 写一条 Turn。
防护本期只做「能否解析为合法 JSON」+ 一组「发现就告警但不阻断」的软检查,不设硬闸门。
"""

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blackboard, Turn

Blackboard_t = dict[str, Any]


@dataclass
class ReducerResult:
    ok: bool
    story_id: str
    turn_index: int | None
    beat_title: str
    blackboard: Blackboard_t  # 本轮结束后的权威黑板(成功为新黑板,失败为保留的旧黑板)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def _check_warnings(old_bb: Blackboard_t, new_bb: Blackboard_t) -> list[str]:
    """非阻断软检查:实体异常缩水 / inventory 与 owner 不一致 / location 指向不存在的场景。"""
    warnings: list[str] = []

    removed = new_bb.get("removed")
    removed_count = len(removed) if isinstance(removed, list) else 0

    # 实体数量缩水(对照上轮)
    if old_bb:
        for cat, label in (("scenes", "场景"), ("characters", "角色"), ("items", "物品")):
            old_n = len(old_bb.get(cat) or {})
            new_n = len(new_bb.get(cat) or {})
            if new_n < old_n:
                warnings.append(
                    f"{label}数量缩水: {old_n} -> {new_n}(removed 显式声明 {removed_count} 项)"
                )

    items = new_bb.get("items") or {}
    scenes = new_bb.get("scenes") or {}
    characters = new_bb.get("characters") or {}

    # inventory 与 item.owner 是同一事实的两个视图,应一致
    for cname, c in characters.items():
        for it in c.get("inventory") or []:
            if it not in items:
                warnings.append(f"inventory/items 不一致: 角色「{cname}」持有「{it}」,但 items 中无此物")
            elif (items.get(it) or {}).get("owner") != f"character:{cname}":
                owner = (items.get(it) or {}).get("owner")
                warnings.append(
                    f"inventory/owner 不一致: 角色「{cname}」持有「{it}」,但其 owner={owner!r}"
                )

    # 角色 location 必须指向存在的场景
    for cname, c in characters.items():
        loc = c.get("location")
        if loc is not None and loc not in scenes:
            warnings.append(f"location 悬空: 角色「{cname}」location={loc!r} 不在 scenes 中")

    return warnings


async def reduce_turn(
    *,
    story_id: str,
    director_b_new_blackboard_str: str,
    writer_narrative: str,
    director_a_json: str,
    user_input: str,
    session: AsyncSession,
) -> ReducerResult:
    # 读旧黑板 + 当前最大 turn_index(turn_index 由代码维护)
    old_row = await session.get(Blackboard, story_id)
    old_bb: Blackboard_t = json.loads(old_row.json_blob) if old_row else {}
    last_idx = (
        await session.execute(
            select(func.max(Turn.turn_index)).where(Turn.story_id == story_id)
        )
    ).scalar()
    next_idx = (last_idx or 0) + 1

    # 1) 解析新黑板;失败则安全降级:保留旧黑板,不写库,不崩溃
    try:
        new_bb: Blackboard_t = json.loads(director_b_new_blackboard_str)
    except json.JSONDecodeError as exc:
        msg = f"新黑板 JSON 解析失败,保留旧黑板,不写库: {exc}"
        print(f"[reducer][ERROR] {msg}")
        return ReducerResult(
            ok=False,
            story_id=story_id,
            turn_index=None,
            beat_title="",
            blackboard=old_bb,
            error=msg,
        )

    # 2) 非阻断告警(发现即打印,但继续)。注意:必须在 strip removed 之前读取告警检查,
    #    因为缩水检查会用到 removed 声明数。
    warnings = _check_warnings(old_bb, new_bb)
    for w in warnings:
        print(f"[reducer][WARN] {w}")

    # 3) 剥离 removed:它是「本轮删除信号」,告警/对照用完即丢,不进入持久化的黑板状态,
    #    以免下一轮 B 在【当前黑板】里看到上轮的 removed。raw 输出仍存于 Turn.director_b_json 备查。
    new_bb.pop("removed", None)

    # 4) 覆盖写 Blackboard(整存,存规范化后的 JSON)
    canonical = json.dumps(new_bb, ensure_ascii=False)
    if old_row:
        old_row.json_blob = canonical
    else:
        session.add(Blackboard(story_id=story_id, json_blob=canonical))

    # 5) 写一条 Turn 存档(director_b_json 保留 B 的原始输出含 removed,供审计)
    beat_title = (new_bb.get("story_meta") or {}).get("latest_beat") or ""
    session.add(
        Turn(
            story_id=story_id,
            turn_index=next_idx,
            beat_title=beat_title,
            user_input=user_input,
            narrative=writer_narrative,
            director_a_json=director_a_json,
            director_b_json=director_b_new_blackboard_str,
            blackboard_after=canonical,
        )
    )
    await session.commit()

    return ReducerResult(
        ok=True,
        story_id=story_id,
        turn_index=next_idx,
        beat_title=beat_title,
        blackboard=new_bb,
        warnings=warnings,
    )
