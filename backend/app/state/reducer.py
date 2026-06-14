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
from app.turns.draw_proposals import persist_draw_proposals

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
    # B 的出图建议(本回合信号,已从持久黑板剥离;交人在回路定夺)
    draw_proposals: list[dict[str, Any]] = field(default_factory=list)


def _stamp_scene_origins(old_bb: Blackboard_t, new_bb: Blackboard_t, turn_index: int) -> None:
    """给场景打「诞生点」origin_turn(M4.5-C 时间倒流语义的地基)。

    规则:本轮新出现的 scene_slug → origin_turn = 本轮;recall 复用的旧场景 → 保留其原
    origin_turn 不变(诞生点不随回访改变)。origin_turn 由 reducer **权威维护**,不信任
    Director-B 的回显——B 全量重写黑板不保证带它,这里就地覆盖正确值。

    注意:场景存续判断(回退/重试用)依据的是 scene.origin_turn,**不是** ImageGen.source_turn;
    后者只是「这张图实际在哪一轮被生成」的审计信息,语义不同,切勿混用。图通过 scene_slug
    间接关联到场景的诞生点,自己不记轮次。
    """
    old_scenes = old_bb.get("scenes") or {}
    for slug, scene in (new_bb.get("scenes") or {}).items():
        if not isinstance(scene, dict):
            continue
        prev = old_scenes.get(slug)
        if isinstance(prev, dict) and "origin_turn" in prev:
            scene["origin_turn"] = prev["origin_turn"]  # 旧场景:保留诞生点(recall 不改)
        else:
            scene["origin_turn"] = turn_index  # 新场景:诞生于本轮


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
    director_a_messages: str = "",
    writer_messages: str = "",
    director_b_messages: str = "",
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

    # 3) 剥离本回合信号字段:removed(删除信号)与 draw_proposals(出图建议)都不属于
    #    持久世界状态,告警/对照/转交人在回路后即从黑板剥离,以免下一轮 B 在【当前黑板】
    #    里看到上轮的它们。raw 输出仍存于 Turn.director_b_json 备查。
    draw_proposals = new_bb.get("draw_proposals")
    draw_proposals = draw_proposals if isinstance(draw_proposals, list) else []
    new_bb.pop("removed", None)
    new_bb.pop("draw_proposals", None)

    # 3.5) 给场景打/继承诞生点 origin_turn(回退/重试的时间倒流地基)
    _stamp_scene_origins(old_bb, new_bb, next_idx)

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
            director_a_messages=director_a_messages,
            writer_messages=writer_messages,
            director_b_messages=director_b_messages,
        )
    )

    # 6) 绘图提案落库成持久待办(M5-B):kind 按场景 origin_turn 权威判定。new_bb 已 stamp 诞生点。
    persist_draw_proposals(
        session, story_id=story_id, turn_index=next_idx, proposals=draw_proposals, blackboard=new_bb
    )

    await session.commit()

    return ReducerResult(
        ok=True,
        story_id=story_id,
        turn_index=next_idx,
        beat_title=beat_title,
        blackboard=new_bb,
        warnings=warnings,
        draw_proposals=draw_proposals,
    )
