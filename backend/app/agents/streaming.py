"""并发合流:把多条「逐 token 流」(label → AsyncIterator[str])汇成单条事件流。

用于 Director-B ∥ Options 这对并行兄弟:两者各自逐 token 产原始 JSON,经本工具交错汇成一条
带 label 的事件流,编排层据 label 把 token 路由到各自节点(保持并行 → 不牺牲延迟)。每条子流
结束(成功或自身异常)各发一个 done 事件;某条子流异常不影响另一条继续(失败语义由调用方按
label 区分:B 失败 abort 整轮、Options 失败仅点红)。
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.agents.director_review import parse_review_output
from app.agents.options import parse_options_output


@dataclass
class StreamEvent:
    label: str
    delta: str | None = None        # token 文本(非结束事件)
    done: bool = False              # 该 label 的子流已结束
    error: Exception | None = None  # 结束且子流自身抛错(done=True 同时携带)


async def merge_streams(streams: dict[str, AsyncIterator[str]]) -> AsyncIterator[StreamEvent]:
    """并发消费多条子流,交错产出 token 事件;每条子流终了各产一个 done 事件(可带 error)。"""
    queue: asyncio.Queue[StreamEvent] = asyncio.Queue()

    async def pump(label: str, agen: AsyncIterator[str]) -> None:
        try:
            async for delta in agen:
                await queue.put(StreamEvent(label=label, delta=delta))
            await queue.put(StreamEvent(label=label, done=True))
        except Exception as exc:  # 子流自身失败 → 作为带 error 的结束事件上报,不连累兄弟流
            await queue.put(StreamEvent(label=label, done=True, error=exc))

    tasks = [asyncio.create_task(pump(label, agen)) for label, agen in streams.items()]
    remaining = len(tasks)
    try:
        while remaining:
            ev = await queue.get()
            if ev.done:
                remaining -= 1
            yield ev
    finally:
        # 调用方提前退出(如 B 失败) → 取消并回收尚在跑的子流,避免悬挂任务噪声。
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


@dataclass
class BOResult:
    """B ∥ Options 合流后的结果(供编排层落盘 / 决定是否 abort)。"""
    new_bb: dict | None = None          # B 的新黑板;None 表示 B 失败(见 b_exc)
    b_exc: Exception | None = None      # B 失败(LLM 报错或 JSON 解析失败)→ 调用方 abort 整轮
    options_json: str = ""              # 成功→OptionsOutput JSON;失败/落空→""(不阻断)


async def stream_b_and_options(
    out: BOResult, *, b_stream: AsyncIterator[str], o_stream: AsyncIterator[str]
) -> AsyncIterator[dict]:
    """并行逐 token 跑 Director-B 与 Options,交错产出 SSE 事件;结果写进 out。

    产出事件:director_b_token / options_token(逐 token)、options_proposed(成功)、
    options_failed(Options 自身失败,不阻断)。B 的成败写入 out.new_bb / out.b_exc,由调用方决定
    abort 与否(B 失败 → 整轮 abort)。两条子流都跑到底再解析(绝不在半截 JSON 上解析)。
    """
    raw: dict[str, list[str]] = {"director_b": [], "options": []}
    async for ev in merge_streams({"director_b": b_stream, "options": o_stream}):
        if ev.delta is not None:
            raw[ev.label].append(ev.delta)
            yield {"type": f"{ev.label}_token", "text": ev.delta}
            continue
        # 子流结束
        if ev.label == "director_b":
            if ev.error is not None:
                out.b_exc = ev.error
            else:
                try:
                    out.new_bb = parse_review_output("".join(raw["director_b"]))
                except Exception as exc:  # JSON 解析失败 → 视同 B 失败,abort
                    out.b_exc = exc
        else:  # options:失败(LLM 或解析)都只点红,不阻断
            if ev.error is not None:
                yield {"type": "options_failed", "reason": f"options: {ev.error}"}
            else:
                try:
                    opts = parse_options_output("".join(raw["options"]))
                    out.options_json = opts.model_dump_json()
                    yield {"type": "options_proposed", "options": opts.options}
                except Exception as exc:
                    yield {"type": "options_failed", "reason": f"options: {exc}"}
