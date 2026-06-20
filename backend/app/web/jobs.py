"""活跃作业登记(内存)—— 让「花钱/改状态的 SSE 流」脱离客户端连接而存活。

问题:回合流与绘图流的真实工作(调 LLM / gpt-image-2、落盘)原本跑在 StreamingResponse 的生成器里;
Starlette 在客户端断开(刷新/关页)时会取消该生成器 → 工作半途夭折:钱已花、结果不落盘、状态不一致。

办法:把生产者(事件 async generator)放进一个**后台任务**跑,任务把事件帧缓冲进 _Job;HTTP 响应只是
「尾随」这个缓冲。客户端断开只取消尾随的响应生成器,**不取消后台任务** → 任务照常跑到底、照常落盘。
前端在重新加载后通过 GET /active 得知尚有作业在跑,轮询 snapshot 直到结果落盘(见前端 recovering)。

登记是内存级:能扛**客户端**刷新/关页(服务端进程不变);扛不住**服务端重启**(那需要把作业状态持久化
到 DB,成本更高,暂不做)。回合每故事至多一个(并发闸:在跑就拒绝新回合);绘图可多个,按 key 区分。
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from fastapi.responses import StreamingResponse

from app.web.sse import SSE_HEADERS


@dataclass
class _Job:
    """一个在跑的流式作业:缓冲已产出的 SSE 帧,供响应尾随;done 后尾随者收尾。"""
    meta: dict[str, Any] = field(default_factory=dict)
    frames: list[str] = field(default_factory=list)  # 已产出的 SSE 帧(已 sse() 格式化)
    done: bool = False
    task: asyncio.Task | None = None
    _updated: asyncio.Event = field(default_factory=asyncio.Event)

    def emit(self, frame: str) -> None:
        self.frames.append(frame)
        self._updated.set()

    def finish(self) -> None:
        self.done = True
        self._updated.set()

    async def tail(self) -> AsyncIterator[str]:
        """尾随:先吐已缓冲帧,再等新帧,done 后收尾。客户端断开时本协程被取消(后台任务不受影响)。"""
        i = 0
        while True:
            if i < len(self.frames):
                frame = self.frames[i]
                i += 1
                yield frame
                continue
            if self.done:
                return
            self._updated.clear()
            if i < len(self.frames) or self.done:  # 清标志与新帧之间的竞态兜底
                continue
            await self._updated.wait()


# 每故事的活跃登记。回合:story_id → _Job(至多一个)。绘图:(story_id, key) → _Job(可多个)。
_turn_jobs: dict[str, _Job] = {}
_draw_jobs: dict[tuple[str, str], _Job] = {}


async def _drive(job: _Job, frames: AsyncIterator[str]) -> None:
    """把生产者跑干、喂进缓冲;不随客户端断开取消 → 跑到底、落盘。异常兜底成一帧 error。"""
    try:
        async for frame in frames:
            job.emit(frame)
    except Exception as exc:  # 生产者内部未捕获异常 → 兜底,避免静默丢失
        from app.web.sse import sse

        job.emit(sse({"type": "error", "reason": str(exc)}))
    finally:
        job.finish()


def _start(registry: dict, key, frames: AsyncIterator[str], meta: dict | None = None) -> _Job:
    job = _Job(meta=meta or {})
    job.task = asyncio.create_task(_drive(job, frames))
    registry[key] = job

    # 任务结束(无论响应是否还连着)→ 从登记里摘除(仅当登记项仍是本作业,避免误删后续同 key 作业)。
    def _cleanup(_t: asyncio.Task) -> None:
        if registry.get(key) is job:
            registry.pop(key, None)

    job.task.add_done_callback(_cleanup)
    return job


def _sse_response(job: _Job) -> StreamingResponse:
    async def _events() -> AsyncIterator[str]:
        async for frame in job.tail():
            yield frame

    return StreamingResponse(_events(), media_type="text/event-stream", headers=SSE_HEADERS)


# ── 回合(submit / retry 共用同一把并发闸)──
def turn_active(story_id: str) -> bool:
    return story_id in _turn_jobs


def start_turn_job(story_id: str, frames: AsyncIterator[str], meta: dict | None = None) -> StreamingResponse:
    return _sse_response(_start(_turn_jobs, story_id, frames, meta))


# ── 绘图(可并发多张,按 key 区分;不设并发闸)──
def start_draw_job(story_id: str, key: str, frames: AsyncIterator[str], meta: dict | None = None) -> StreamingResponse:
    return _sse_response(_start(_draw_jobs, (story_id, key), frames, meta))


def active_status(story_id: str) -> dict:
    """该故事此刻在跑的作业:回合(含 meta,供前端占位展示)+ 绘图条数/键(供前端进度提示)。"""
    turn = _turn_jobs.get(story_id)
    draws = [{"key": k[1], **j.meta} for k, j in _draw_jobs.items() if k[0] == story_id]
    return {
        "turn": ({"active": True, **turn.meta} if turn else None),
        "draws": draws,
    }
