"""每步完整上下文的读取与清理(M4.5-B)。

每轮三次 LLM 调用(Director-A / Writer / Director-B)真正喂进去的完整 messages 数组,
随该轮一起存在 Turn 表的 director_a_messages / writer_messages / director_b_messages 里。
这是 React Flow「点进节点看完整上下文」的数据地基,也是回退/重试复用历史的基础。

⚠️ 平方增长风险:每轮都把当时的全量历史 + system + 易变区原样存三份,存储量随
   轮数×每轮历史(整体≈O(n^2))增长。现阶段单人本地、轮数少,不痛,不做过早优化;
   清理策略待定——下手点就是下面的 prune_step_contexts。
"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context import Message
from app.db.models import Turn

# agent 步 → Turn 表对应列名(options 是 Writer 后与 B 并行的叶子,显微镜可看/可编辑重试)
_STEP_COLUMNS = {
    "director_a": "director_a_messages",
    "writer": "writer_messages",
    "director_b": "director_b_messages",
    "options": "options_messages",
}


async def get_step_contexts(
    session: AsyncSession, story_id: str, turn_index: int
) -> dict[str, list[Message]] | None:
    """取某一轮三个 agent 各自当时喂给 LLM 的完整 messages。

    返回 {"director_a": [...], "writer": [...], "director_b": [...]};该轮不存在返回 None;
    某步未存(老数据/空串)则该键为空列表 []。
    """
    turn = (
        await session.execute(
            select(Turn).where(Turn.story_id == story_id, Turn.turn_index == turn_index)
        )
    ).scalar_one_or_none()
    if turn is None:
        return None
    out: dict[str, list[Message]] = {}
    for step, col in _STEP_COLUMNS.items():
        raw = getattr(turn, col) or ""
        out[step] = json.loads(raw) if raw else []
    return out


async def set_step_context(
    session: AsyncSession, story_id: str, turn_index: int, step: str, messages: list[Message]
) -> bool:
    """把某一轮某一步的输入记录(完整 messages)就地改写为 messages —— **直接改这一步存的那份
    记录本身**,不是独立补丁层(改完存的就是改后内容,刷新还在)。

    只写 _STEP_COLUMNS[step] 这一列,绝不顺着内容关联去动上游的输出记录:用户改的是当前节点的
    输入记录,上游 agent 的输出(director_a_json / narrative / blackboard_after)原样不动。
    未知 step 或该轮不存在 → 返回 False。
    """
    col = _STEP_COLUMNS.get(step)
    if col is None:
        return False
    turn = (
        await session.execute(
            select(Turn).where(Turn.story_id == story_id, Turn.turn_index == turn_index)
        )
    ).scalar_one_or_none()
    if turn is None:
        return False
    setattr(turn, col, json.dumps(messages, ensure_ascii=False))
    await session.commit()
    return True


async def prune_step_contexts(
    session: AsyncSession, story_id: str, *, keep_recent_n: int
) -> int:
    """清理钩子(应对上面的平方增长风险)。把除最近 keep_recent_n 轮以外的旧轮的三份
    messages 置空(清掉大块上下文,但保留 Turn 行本身的叙事/黑板等轻量字段)。

    返回被清理的轮数。**清理策略尚未定**(何时触发、保留多少、是否归档到冷存储等),
    本函数是预留的最简下手点;现阶段不在任何流程里自动调用。
    """
    turns = (
        await session.execute(
            select(Turn).where(Turn.story_id == story_id).order_by(Turn.turn_index.desc())
        )
    ).scalars().all()
    pruned = 0
    for turn in turns[keep_recent_n:]:
        if turn.director_a_messages or turn.writer_messages or turn.director_b_messages:
            turn.director_a_messages = ""
            turn.writer_messages = ""
            turn.director_b_messages = ""
            pruned += 1
    if pruned:
        await session.commit()
    return pruned
