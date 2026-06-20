"""重试(retry)——时间操作之一(M4.5-C-4)。

在当前最新一轮,从指定切入点重走,复用现有三段式执行逻辑(stream_director / stream_writer /
stream_director_review / stream_options / reduce_turn),逐 token 流式产出、不另写一套 agent 调用:

  - 从 A 前(director_a) → 整轮重来:A→Writer→(B ∥ Options)
  - 从 Writer 前(writer) → 保留本轮 A,重走 Writer→(B ∥ Options)
  - 从 B 前(director_b) → 保留本轮 A、Writer,只重走 B(Options 保留不动)
  - 从 Options(options) → 叶子自重试:只重跑 Options 本身,不 rollback/reduce、不碰 B/场景/叙事

Options 是 Writer 后与 B 并行的叶子(依赖 Writer 成稿 + tips):上游(A/Writer)重走 → 连带重跑
Options;B 与 Options 是并行兄弟,互不影响(B 重走不动 Options,Options 重走不动 B)。

切入点之后的旧结果直接丢弃、用重走的新结果覆盖(不留旧结果做历史;要保留请用副本)。
重走复用 M4.5-B 存的该轮上下文:**被保留的前序结果不变 → 第一个重走的 agent 的上下文
与原先逐字节相同,直接复用存档的 messages(缓存命中)**;其上游一旦变化(如新 A 改了 brief、
新 Writer 改了成稿),下游 agent 的上下文按现行结果重新构造。

场景(核心规则):前三个切入点(A/Writer/B)都会重走 B,故每次都:作废本轮原 B 诞生的场景(随黑板回滚
到本轮之前自然消失,图引用解除、ImageGen 记录与磁盘文件保留)+ 新 B 重新诞生场景
(reduce 给新场景打 origin_turn=本轮)。复用 rollback_latest_turn 做作废、reduce_turn 做新生。

回合内顺序约束不变:A/Writer/B 全部读「本轮之前」的同一份黑板+历史(只读,在内存里跑完
三段),之后才动 DB(回滚原轮 + reduce 写新轮)。缓存布局不变。
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.bibles import resolve_style_bible
from app.agents.context import build_messages
from app.agents.director import parse_director_output, stream_director
from app.agents.director_review import parse_review_output, stream_director_review
from app.agents.options import parse_options_output, stream_options
from app.agents.writer import stream_writer
from app.db.models import Turn
from app.knowledge.store import get_knowledge
from app.models.schemas import DirectorOutput
from app.state.reducer import reduce_turn
from app.stories.settings_store import get_or_create_settings, resolve_agent_model
from app.stories.store import empty_blackboard
from app.turns.rollback import rollback_latest_turn
from app.turns.scene_origins import scenes_born_in_turn

ENTRY_POINTS = ("director_a", "writer", "director_b", "options")


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


async def stream_retry_turn(session: AsyncSession, story_id: str, entry: str) -> AsyncIterator[dict]:
    """重试的流式版本:逐 token 产各重走 agent 的输出,最后一次性动 DB(失败前 DB 不变 → 安全)。

    产出事件(dict,供 SSE 包装):
      - retry_started {entry, turn_index}
      - director_a_token / narrative_token / narrative_done / director_b_token / options_token(逐 token)
      - options_proposed / options_failed
      - state_updated {blackboard, beat_title}(reduce 落盘后;options 叶子重试无此事件)
      - retry_done {entry, turn_index, narrative, blackboard, invalidated_scene_slugs, new_scene_slugs}
      - error {reason}(任一步失败;此前 DB 未改 → 原轮完好,调用方据此恢复显示)

    切入点语义与原 retry_turn 完全一致(见模块 docstring);只是把「跑完再返回」改成「边跑边产事件」。
    B 与 Options 顺序重走(B 先、O 后),各自逐 token —— 重试是用户主动操作,非热路径,顺序流更易观察。
    """
    if entry not in ENTRY_POINTS:
        yield {"type": "error", "reason": f"未知切入点: {entry!r}(应为 {ENTRY_POINTS})"}
        return

    turns_desc = (
        await session.execute(
            select(Turn).where(Turn.story_id == story_id).order_by(Turn.turn_index.desc())
        )
    ).scalars().all()
    if not turns_desc:
        yield {"type": "error", "reason": "没有可重试的回合"}
        return
    turn_n = turns_desc[0]
    n = turn_n.turn_index
    user_input = turn_n.user_input
    yield {"type": "retry_started", "entry": entry, "turn_index": n}

    # 本轮之前的黑板与历史(A/Writer/B 共享这同一份;只读,先在内存跑完三段再动 DB)
    prev = turns_desc[1] if len(turns_desc) > 1 else None
    if prev is not None:
        pre_bb_str = prev.blackboard_after
    else:
        pre_bb_str = json.dumps(empty_blackboard(), ensure_ascii=False)  # 标题不在黑板,无需保留
    pre_bb = json.loads(pre_bb_str)
    history = await _history_before(session, story_id, n)
    knowledge = await get_knowledge(session, story_id)
    # 故事内模型设置:重走的 agent 也按各自设置取模型(默认全 deepseek)。
    st = await get_or_create_settings(session, story_id)
    style_bible = resolve_style_bible(st.style_bible)  # 故事自定义文风圣经(空则全局默认)
    model_a = resolve_agent_model(st, "director_a")
    model_w = resolve_agent_model(st, "writer")
    model_b = resolve_agent_model(st, "director_b")
    model_o = resolve_agent_model(st, "options")

    # ---- Options 叶子自重试:只重跑 Options,不动 B/场景/叙事/黑板 ----
    # 它依赖本轮 Writer 成稿 + A 的 tips(都保留)→ 上下文与原先逐字节相同,复用存档 messages(缓存命中)。
    # 就地覆写 turn_n 的 options 两列即可,无 rollback、无 reduce。
    if entry == "options":
        a = DirectorOutput.model_validate_json(turn_n.director_a_json)
        narrative = turn_n.narrative
        o_messages = json.loads(turn_n.options_messages or "[]") or build_messages(
            "options", history=history, blackboard=pre_bb, user_action=user_input,
            narrative=narrative, tips=a.tips, style_bible=style_bible,
        )
        o_chunks: list[str] = []
        try:
            async for tok in stream_options(
                history, pre_bb, user_input, narrative, tips=a.tips, messages=o_messages, model=model_o
            ):
                o_chunks.append(tok)
                yield {"type": "options_token", "text": tok}
            options_out = parse_options_output("".join(o_chunks))
        except Exception as exc:  # 叶子自重试失败:不落盘(保留原选项),上报错误
            yield {"type": "error", "reason": f"options: {exc}"}
            return
        turn_n.options_json = options_out.model_dump_json()
        turn_n.options_messages = json.dumps(o_messages, ensure_ascii=False)
        await session.commit()
        yield {"type": "options_proposed", "options": options_out.options}
        yield {
            "type": "retry_done", "entry": "options", "turn_index": n, "narrative": narrative,
            "blackboard": json.loads(turn_n.blackboard_after) if turn_n.blackboard_after else {},
            "invalidated_scene_slugs": [], "new_scene_slugs": [],
        }
        return

    # ---- Director-A:新走(director_a 切入)或保留 ----
    # A 的上下文只取决于「本轮之前的状态」,重试时不变 → 存档的 director_a_messages 始终是其正确上下文。
    a_messages = json.loads(turn_n.director_a_messages or "[]") or build_messages(
        "director", history=history, blackboard=pre_bb, user_action=user_input,
        knowledge=knowledge, style_bible=style_bible,
    )
    if entry == "director_a":
        a_chunks: list[str] = []
        try:
            async for tok in stream_director(
                history, pre_bb, user_input, knowledge=knowledge, messages=a_messages, model=model_a
            ):
                a_chunks.append(tok)
                yield {"type": "director_a_token", "text": tok}
            a = parse_director_output("".join(a_chunks))
        except Exception as exc:
            yield {"type": "error", "reason": f"director-a: {exc}"}
            return
    else:
        a = DirectorOutput.model_validate_json(turn_n.director_a_json)

    # ---- Writer:新走(director_a / writer 切入)或保留 ----
    if entry in ("director_a", "writer"):
        if entry == "writer":
            # A 保留 → Writer 上下文与原先逐字节相同 → 复用存档 messages(缓存命中)
            w_messages = json.loads(turn_n.writer_messages or "[]") or build_messages(
                "writer", history=history, blackboard=pre_bb, user_action=user_input,
                writing_brief=a.writing_brief, tips=a.tips, style_bible=style_bible,
            )
        else:
            # A 是新的 → brief / tips 变了 → 按新 brief 重建 Writer 上下文
            w_messages = build_messages(
                "writer", history=history, blackboard=pre_bb, user_action=user_input,
                writing_brief=a.writing_brief, tips=a.tips, style_bible=style_bible,
            )
        chunks: list[str] = []
        try:
            async for tok in stream_writer(history, pre_bb, user_input, a.writing_brief, messages=w_messages, model=model_w):
                chunks.append(tok)
                yield {"type": "narrative_token", "text": tok}
        except Exception as exc:
            yield {"type": "error", "reason": f"writer: {exc}"}
            return
        narrative = "".join(chunks)
        yield {"type": "narrative_done", "full_narrative": narrative}
    else:  # director_b:保留 Writer 成稿
        narrative = turn_n.narrative
        w_messages = json.loads(turn_n.writer_messages or "[]")

    # ---- Director-B:总是重走(逐 token)----
    if entry == "director_b":
        # A、Writer 均保留 → B 上下文与原先逐字节相同 → 复用存档 messages(缓存命中)
        b_messages = json.loads(turn_n.director_b_messages or "[]") or build_messages(
            "director_review", history=history, blackboard=pre_bb, user_action=user_input,
            narrative=narrative, director_a_plan=a.model_dump(), tips=a.tips, style_bible=style_bible,
        )
    else:
        # 上游(A 或 Writer)变了 → 按现行成稿/预案重建 B 上下文
        b_messages = build_messages(
            "director_review", history=history, blackboard=pre_bb, user_action=user_input,
            narrative=narrative, director_a_plan=a.model_dump(), tips=a.tips, style_bible=style_bible,
        )
    b_chunks: list[str] = []
    try:
        async for tok in stream_director_review(
            history, pre_bb, user_input, narrative, director_a_plan=a.model_dump(),
            tips=a.tips, messages=b_messages, model=model_b,
        ):
            b_chunks.append(tok)
            yield {"type": "director_b_token", "text": tok}
        new_bb = parse_review_output("".join(b_chunks))
    except Exception as exc:
        yield {"type": "error", "reason": f"director-b: {exc}"}
        return

    # ---- Options(B 的并行兄弟):director_b 切入 → 保留原 Options;上游(A/Writer)切入 → 连带重跑 ----
    # 必须在 rollback 删 turn_n 之前把要保留的两列读进局部变量。重跑失败不阻断重试(options 落空)。
    if entry == "director_b":
        options_json = turn_n.options_json or ""
        options_messages = turn_n.options_messages or ""
    else:
        o_messages = build_messages(
            "options", history=history, blackboard=pre_bb, user_action=user_input,
            narrative=narrative, tips=a.tips, style_bible=style_bible,
        )
        oo_chunks: list[str] = []
        try:
            async for tok in stream_options(
                history, pre_bb, user_input, narrative, tips=a.tips, messages=o_messages, model=model_o
            ):
                oo_chunks.append(tok)
                yield {"type": "options_token", "text": tok}
            options_out = parse_options_output("".join(oo_chunks))
            options_json = options_out.model_dump_json()
            yield {"type": "options_proposed", "options": options_out.options}
        except Exception as exc:  # Options 失败不阻断重试(options 落空)
            options_json = ""
            yield {"type": "options_failed", "reason": f"options: {exc}"}
        options_messages = json.dumps(o_messages, ensure_ascii=False)

    # ---- 全部跑完,才动 DB ----
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
        options_json=options_json,
        options_messages=options_messages,
    )
    new_scenes = scenes_born_in_turn(result.blackboard, result.turn_index)
    yield {"type": "state_updated", "blackboard": result.blackboard, "beat_title": result.beat_title}
    yield {
        "type": "retry_done", "entry": entry, "turn_index": result.turn_index, "narrative": narrative,
        "blackboard": result.blackboard, "invalidated_scene_slugs": invalidated, "new_scene_slugs": new_scenes,
    }


async def retry_turn(session: AsyncSession, story_id: str, entry: str) -> RetryResult:
    """非流式包装:把 stream_retry_turn 跑干,归并成 RetryResult(供非 SSE 调用方 / 单测复用)。"""
    result = RetryResult(ok=False)
    async for ev in stream_retry_turn(session, story_id, entry):
        if ev["type"] == "retry_done":
            result = RetryResult(
                ok=True, entry=ev["entry"], turn_index=ev["turn_index"], narrative=ev["narrative"],
                blackboard=ev["blackboard"], invalidated_scene_slugs=ev["invalidated_scene_slugs"],
                new_scene_slugs=ev["new_scene_slugs"],
            )
        elif ev["type"] == "error":
            return RetryResult(ok=False, reason=ev["reason"])
    return result
