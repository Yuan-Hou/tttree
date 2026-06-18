"""Options agent(里程碑 Part 2):Writer 后与 Director-B 并行的「下一步选项」叶子。

覆盖:① options 角色的上下文(成稿 + 黑板 + tips 在易变尾部,前缀与 B 一致);② 并行编排
(B 与 Options 都完才 turn_done,options_proposed 独立点亮,落 options 列);③ Options 失败不阻断
落盘(reducer 只等 B);④ 显微镜读到 options。
"""

import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from app.agents.context import build_messages
from app.db.models import Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.models.schemas import OptionsOutput
from app.stories.store import create_story


def _bb():
    return {"story_meta": {"title": "T", "current_scene": "", "latest_beat": "开端"},
            "scenes": {}, "characters": {}, "items": {}, "notes": []}


# ── 上下文:options 角色 ───────────────────────────────────────
def test_options_context_has_narrative_tips_and_shares_prefix():
    bb = {"scenes": {"lab": {"name": "实验室", "state": "亮着"}}}
    common = dict(history=[{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}],
                  blackboard=bb, user_action="走进去")
    opt = build_messages("options", narrative="他推开门,看见白子。", tips=["白子爱用敬语"], **common)
    b = build_messages("director_review", narrative="他推开门,看见白子。", tips=["白子爱用敬语"], **common)
    tail = opt[-1]["content"]
    assert "推开门" in tail and "敬语" in tail  # 成稿 + tips 都在
    assert tail.index("敬语") > tail.index("当前黑板")  # tips 在易变尾部
    # 缓存前缀:system + history 与 B 逐字节一致(只有任务尾部不同)
    assert opt[:-1] == b[:-1]


# ── 并行编排:用 ASGI 跑真实 _turn_events ─────────────────────
@pytest.fixture
async def turn_env(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'opt.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.web.turn_router.async_session", Session)  # 写:POST /turn
    monkeypatch.setattr("app.db.session.async_session", Session)  # 读:GET /contexts(经 deps.get_session)

    state: dict = {"options_delay": 0.0, "options_raise": False, "b_order": [], "o_order": []}

    async def fake_director(*a, **k):
        from app.models.schemas import DirectorOutput
        return DirectorOutput(situation="s", beat_points=["b"], writing_brief="brief", tips=["白子爱用敬语"])

    async def fake_writer(*a, **k):
        for ch in "一段叙事":
            yield ch

    async def fake_review(*a, **k):
        state["b_order"].append("done")
        return _bb()

    async def fake_options(*a, **k):
        await asyncio.sleep(state["options_delay"])
        state["o_order"].append("done")
        if state["options_raise"]:
            from app.agents.options import OptionsError
            raise OptionsError("boom", raw="{}")
        return OptionsOutput(options=["往前走", "退回去"])

    monkeypatch.setattr("app.web.turn_router.run_director", fake_director)
    monkeypatch.setattr("app.web.turn_router.stream_writer", fake_writer)
    monkeypatch.setattr("app.web.turn_router.run_director_review", fake_review)
    monkeypatch.setattr("app.web.turn_router.run_options", fake_options)

    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, Session, state
    await engine.dispose()


async def test_options_runs_parallel_persists_and_lights_independently(turn_env):
    c, Session, state = turn_env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id

    r = await c.post(f"/story/{sid}/turn", json={"user_input": "走进门"})
    assert r.status_code == 200
    body = r.text
    # options_proposed 事件带选项;turn_done 在两者都完成后才发
    assert "options_proposed" in body and "往前走" in body
    assert "turn_done" in body
    assert state["b_order"] and state["o_order"]  # 两个都真的跑了

    # 落盘:options_json / options_messages 都写入了 Turn 行
    async with Session() as s:
        t = (await s.execute(select(Turn).where(Turn.story_id == sid))).scalar_one()
    assert json.loads(t.options_json)["options"] == ["往前走", "退回去"]
    assert json.loads(t.options_messages)  # 喂给 Options 的完整 messages 已存档


async def test_slow_options_still_awaited_before_turn_done(turn_env):
    c, Session, state = turn_env
    state["options_delay"] = 0.15  # Options 比 B 慢 → 仍要等它
    async with Session() as s:
        sid = (await create_story(s, title="T")).id

    body = (await c.post(f"/story/{sid}/turn", json={"user_input": "go"})).text
    # turn_done 出现在 options_proposed 之后(整流等到 Options 也结束)
    assert body.index("options_proposed") < body.index("turn_done")
    async with Session() as s:
        t = (await s.execute(select(Turn).where(Turn.story_id == sid))).scalar_one()
    assert json.loads(t.options_json)["options"] == ["往前走", "退回去"]


async def test_options_failure_does_not_block_persistence(turn_env):
    c, Session, state = turn_env
    state["options_raise"] = True
    async with Session() as s:
        sid = (await create_story(s, title="T")).id

    body = (await c.post(f"/story/{sid}/turn", json={"user_input": "go"})).text
    assert "options_failed" in body  # 点红 options 节点
    assert "turn_done" in body  # 但本轮照常完成(reducer 只等 B)
    async with Session() as s:
        t = (await s.execute(select(Turn).where(Turn.story_id == sid))).scalar_one()
    assert t.narrative == "一段叙事"  # 已落盘
    assert t.options_json == ""  # Options 落空,不阻断


async def test_snapshot_restores_latest_round_options(turn_env):
    """常驻可调取:GET /snapshot 回传最新一轮的 options(刷新/切故事后据此恢复选项条)。"""
    c, Session, state = turn_env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    # 无回合时为空,不报错
    assert (await c.get(f"/story/{sid}/snapshot")).json()["latest_options"] == []

    await c.post(f"/story/{sid}/turn", json={"user_input": "go"})
    snap = (await c.get(f"/story/{sid}/snapshot")).json()
    assert snap["latest_options"] == ["往前走", "退回去"]


async def test_snapshot_options_empty_when_round_failed(turn_env):
    """当轮 Options 失败/落空 → latest_options 为空,优雅降级。"""
    c, Session, state = turn_env
    state["options_raise"] = True
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await c.post(f"/story/{sid}/turn", json={"user_input": "go"})
    assert (await c.get(f"/story/{sid}/snapshot")).json()["latest_options"] == []


async def test_microscope_exposes_options(turn_env):
    c, Session, state = turn_env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await c.post(f"/story/{sid}/turn", json={"user_input": "go"})

    ctx = (await c.get(f"/story/{sid}/turn/1/contexts")).json()
    assert "options" in ctx
    assert ctx["options"]["output"]["options"] == ["往前走", "退回去"]
    assert ctx["options"]["messages"]  # 完整输入可见
