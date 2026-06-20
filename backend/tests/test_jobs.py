"""活跃作业登记:断连存活(无消费者也跑到底)+ 活跃状态上报 + 尾随回放。

核心保障:把花钱/改状态的 SSE 流挪进后台任务,客户端断开(刷新/关页)只取消「尾随的响应」,
不取消后台任务 → 工作照常跑完落盘。这里直接对 jobs 模块做单测(不打真 LLM/真出图)。
"""

import asyncio

import pytest

from app.web import jobs


@pytest.fixture(autouse=True)
async def _clean_registries():
    jobs._turn_jobs.clear()
    jobs._draw_jobs.clear()
    yield
    # 取消并回收遗留后台任务,避免跨用例泄漏 / 悬挂任务告警
    leftover = [
        j.task
        for reg in (jobs._turn_jobs, jobs._draw_jobs)
        for j in reg.values()
        if j.task and not j.task.done()
    ]
    for t in leftover:
        t.cancel()
    for t in leftover:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    jobs._turn_jobs.clear()
    jobs._draw_jobs.clear()


async def _wait(pred, tries=400):
    for _ in range(tries):
        await asyncio.sleep(0.005)
        if pred():
            return
    raise AssertionError("等待超时")


async def test_turn_job_completes_without_consumer():
    """没有人消费响应(模拟客户端断开)→ 后台生产者仍跑到底,完成后自动从登记摘除。"""
    ran = []

    async def producer():
        for i in range(3):
            await asyncio.sleep(0)
            ran.append(i)
            yield f"data: {i}\n\n"

    jobs.start_turn_job("s1", producer(), meta={"kind": "turn"})  # 拿到响应但故意不消费
    assert jobs.turn_active("s1")  # 登记在案 → 并发闸生效
    await _wait(lambda: not jobs.turn_active("s1"))
    assert ran == [0, 1, 2]  # 断连存活:跑到底


async def test_active_status_reports_turn_and_draws():
    async def slow():
        await asyncio.sleep(0.4)
        yield "data: x\n\n"

    jobs.start_turn_job("sA", slow(), meta={"kind": "turn", "user_input": "走"})
    jobs.start_draw_job("sA", "picture:7", slow(), meta={"kind": "picture", "proposal_id": 7})

    st = jobs.active_status("sA")
    assert st["turn"] == {"active": True, "kind": "turn", "user_input": "走"}
    assert st["draws"] == [{"key": "picture:7", "kind": "picture", "proposal_id": 7}]
    # 故事隔离:别的故事看不到
    assert jobs.active_status("other") == {"turn": None, "draws": []}


async def test_tail_replays_buffer_then_follows_to_done():
    async def producer():
        yield "data: 1\n\n"
        await asyncio.sleep(0.02)
        yield "data: 2\n\n"

    jobs.start_turn_job("sT", producer())
    job = jobs._turn_jobs["sT"]
    got = [frame async for frame in job.tail()]  # 回放已缓冲 + 跟随到 done
    assert got == ["data: 1\n\n", "data: 2\n\n"]


async def test_turn_active_clears_after_finish_for_guard():
    async def quick():
        yield "data: ok\n\n"

    jobs.start_turn_job("sG", quick())
    await _wait(lambda: not jobs.turn_active("sG"))
    assert not jobs.turn_active("sG")  # 完成即释放并发闸,可再发起
