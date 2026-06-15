"""重试(retry)——时间操作之一(M4.5-C-4)。

在当前最新一轮,从指定切入点重走,复用现有三段式执行逻辑(run_director / stream_writer /
run_director_review / reduce_turn),不另写一套 agent 调用:

  - 从 A 前(director_a) → 整轮重来:A→Writer→B
  - 从 Writer 前(writer) → 保留本轮 A,重走 Writer→B
  - 从 B 前(director_b) → 保留本轮 A、Writer,只重走 B

切入点之后的旧结果直接丢弃、用重走的新结果覆盖(不留旧结果做历史;要保留请用副本)。
重走复用 M4.5-B 存的该轮上下文:**被保留的前序结果不变 → 第一个重走的 agent 的上下文
与原先逐字节相同,直接复用存档的 messages(缓存命中)**;其上游一旦变化(如新 A 改了 brief、
新 Writer 改了成稿),下游 agent 的上下文按现行结果重新构造。

场景(核心规则):重试的三个切入点都会重走 B,故每次都:作废本轮原 B 诞生的场景(随黑板回滚
到本轮之前自然消失,图引用解除、ImageGen 记录与磁盘文件保留)+ 新 B 重新诞生场景
(reduce 给新场景打 origin_turn=本轮)。复用 rollback_latest_turn 做作废、reduce_turn 做新生。

回合内顺序约束不变:A/Writer/B 全部读「本轮之前」的同一份黑板+历史(只读,在内存里跑完
三段),之后才动 DB(回滚原轮 + reduce 写新轮)。缓存布局不变。
"""

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context import build_messages
from app.agents.director import run_director
from app.agents.director_review import run_director_review
from app.agents.writer import stream_writer
from app.db.models import Blackboard, Turn
from app.knowledge.store import get_knowledge
from app.models.schemas import DirectorOutput
from app.state.reducer import reduce_turn
from app.stories.settings_store import get_or_create_settings, resolve_agent_model
from app.stories.store import empty_blackboard
from app.turns.rollback import rollback_latest_turn
from app.turns.scene_origins import scenes_born_in_turn

ENTRY_POINTS = ("director_a", "writer", "director_b")


@dataclass
class RetryResult:
    ok: bool
    entry: str | None = None
    turn_index: int | None = None
    narrative: str = ""
    blackboard: dict = field(default_factory=dict)
    invalidated_scene_slugs: list[str] = field(default_factory=list)  # 原 B 诞生、被作废的场景
    new_scene_slugs: list[str] = field(default_factory=list)          # 新 B 诞生的场景
    reason: str | None = None


async def _history_before(session: AsyncSession, story_id: str, n: int) -> list[dict]:
    """重建本轮之前(turn_index < n)的干净历史(user=玩家输入, assistant=叙事)。"""
    turns = (
        await session.execute(
            select(Turn).where(Turn.story_id == story_id, Turn.turn_index < n).order_by(Turn.turn_index)
        )
    ).scalars().all()
    history: list[dict] = []
    for t in turns:
        history.append({"role": "user", "content": t.user_input})
        history.append({"role": "assistant", "content": t.narrative})
    return history


async def retry_turn(session: AsyncSession, story_id: str, entry: str) -> RetryResult:
    if entry not in ENTRY_POINTS:
        return RetryResult(ok=False, reason=f"未知切入点: {entry!r}(应为 {ENTRY_POINTS})")

    turns_desc = (
        await session.execute(
            select(Turn).where(Turn.story_id == story_id).order_by(Turn.turn_index.desc())
        )
    ).scalars().all()
    if not turns_desc:
        return RetryResult(ok=False, reason="没有可重试的回合")
    turn_n = turns_desc[0]
    n = turn_n.turn_index
    user_input = turn_n.user_input

    # 本轮之前的黑板与历史(A/Writer/B 共享这同一份;只读,先在内存跑完三段再动 DB)
    prev = turns_desc[1] if len(turns_desc) > 1 else None
    if prev is not None:
        pre_bb_str = prev.blackboard_after
    else:
        cur = await session.get(Blackboard, story_id)
        title = (json.loads(cur.json_blob).get("story_meta") or {}).get("title", "") if cur else ""
        pre_bb_str = json.dumps(empty_blackboard(title), ensure_ascii=False)
    pre_bb = json.loads(pre_bb_str)
    history = await _history_before(session, story_id, n)
    knowledge = await get_knowledge(session, story_id)
    # 故事内模型设置:重走的 agent 也按各自设置取模型(默认全 deepseek)。
    st = await get_or_create_settings(session, story_id)
    model_a = resolve_agent_model(st, "director_a")
    model_w = resolve_agent_model(st, "writer")
    model_b = resolve_agent_model(st, "director_b")

    # ---- Director-A:新走(director_a 切入)或保留 ----
    # A 的上下文只取决于「本轮之前的状态」,重试时不变 → 存档的 director_a_messages 始终是其正确上下文。
    a_messages = json.loads(turn_n.director_a_messages or "[]") or build_messages(
        "director", history=history, blackboard=pre_bb, user_action=user_input, knowledge=knowledge
    )
    if entry == "director_a":
        a = await run_director(history, pre_bb, user_input, knowledge=knowledge, messages=a_messages, model=model_a)
    else:
        a = DirectorOutput.model_validate_json(turn_n.director_a_json)

    # ---- Writer:新走(director_a / writer 切入)或保留 ----
    if entry in ("director_a", "writer"):
        if entry == "writer":
            # A 保留 → Writer 上下文与原先逐字节相同 → 复用存档 messages(缓存命中)
            w_messages = json.loads(turn_n.writer_messages or "[]") or build_messages(
                "writer", history=history, blackboard=pre_bb, user_action=user_input, writing_brief=a.writing_brief
            )
        else:
            # A 是新的 → brief 变了 → 按新 brief 重建 Writer 上下文
            w_messages = build_messages(
                "writer", history=history, blackboard=pre_bb, user_action=user_input, writing_brief=a.writing_brief
            )
        chunks: list[str] = []
        async for tok in stream_writer(history, pre_bb, user_input, a.writing_brief, messages=w_messages, model=model_w):
            chunks.append(tok)
        narrative = "".join(chunks)
    else:  # director_b:保留 Writer 成稿
        narrative = turn_n.narrative
        w_messages = json.loads(turn_n.writer_messages or "[]")

    # ---- Director-B:总是重走 ----
    if entry == "director_b":
        # A、Writer 均保留 → B 上下文与原先逐字节相同 → 复用存档 messages(缓存命中)
        b_messages = json.loads(turn_n.director_b_messages or "[]") or build_messages(
            "director_review", history=history, blackboard=pre_bb, user_action=user_input,
            narrative=narrative, director_a_plan=a.model_dump(),
        )
    else:
        # 上游(A 或 Writer)变了 → 按现行成稿/预案重建 B 上下文
        b_messages = build_messages(
            "director_review", history=history, blackboard=pre_bb, user_action=user_input,
            narrative=narrative, director_a_plan=a.model_dump(),
        )
    new_bb = await run_director_review(
        history, pre_bb, user_input, narrative, director_a_plan=a.model_dump(), messages=b_messages, model=model_b
    )

    # ---- 三段跑完,才动 DB ----
    # 1) 作废原轮:回滚黑板到本轮之前 + 删除原 Turn N → 原 B 诞生的场景随之消失(图资产保留)。
    rb = await rollback_latest_turn(session, story_id)
    invalidated = rb.released_scene_slugs
    # 2) 新生:reduce 重建 Turn N(turn_index 复用 N),给新场景打 origin_turn=N。
    result = await reduce_turn(
        story_id=story_id,
        director_b_new_blackboard_str=json.dumps(new_bb, ensure_ascii=False),
        writer_narrative=narrative,
        director_a_json=a.model_dump_json(),
        user_input=user_input,
        session=session,
        director_a_messages=json.dumps(a_messages, ensure_ascii=False),
        writer_messages=json.dumps(w_messages, ensure_ascii=False),
        director_b_messages=json.dumps(b_messages, ensure_ascii=False),
    )

    return RetryResult(
        ok=True,
        entry=entry,
        turn_index=result.turn_index,
        narrative=narrative,
        blackboard=result.blackboard,
        invalidated_scene_slugs=invalidated,
        new_scene_slugs=scenes_born_in_turn(result.blackboard, result.turn_index),
    )
