import json

from app.agents.context import build_messages
from app.db.session import create_all, make_engine, make_session_factory
from app.state.reducer import reduce_turn
from app.turns.step_contexts import get_step_contexts, prune_step_contexts

_BB = {"story_meta": {"current_scene": "room"}, "scenes": {}, "characters": {}, "items": {}, "notes": []}
_NEW_BB = json.dumps(
    {"story_meta": {"latest_beat": "第一拍"}, "scenes": {}, "characters": {}, "items": {}, "notes": []},
    ensure_ascii=False,
)


async def _setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'steps.db'}")
    await create_all(engine)
    return make_session_factory(engine)


async def test_step_contexts_stored_and_faithful(tmp_path):
    """跑一轮:三份完整 messages 都被存下、是合法完整数组,且取回的就是当时 build_messages 的输出。"""
    Session = await _setup(tmp_path)
    history = []
    a_msgs = build_messages("director", history=history, blackboard=_BB, user_action="走进去", knowledge="KB哨兵串")
    w_msgs = build_messages("writer", history=history, blackboard=_BB, user_action="走进去", writing_brief="wb")
    b_msgs = build_messages("director_review", history=history, blackboard=_BB, user_action="走进去",
                            narrative="一段叙事", director_a_plan={"situation": "s"})

    async with Session() as s:
        result = await reduce_turn(
            story_id="st", director_b_new_blackboard_str=_NEW_BB, writer_narrative="一段叙事",
            director_a_json="{}", user_input="走进去", session=s,
            director_a_messages=json.dumps(a_msgs, ensure_ascii=False),
            writer_messages=json.dumps(w_msgs, ensure_ascii=False),
            director_b_messages=json.dumps(b_msgs, ensure_ascii=False),
        )
        assert result.ok
        ctx = await get_step_contexts(s, "st", result.turn_index)

    assert ctx is not None
    # 三份都在,各是合法完整 messages 数组(system 开头 + 每条都有 role/content)
    for step in ("director_a", "writer", "director_b"):
        msgs = ctx[step]
        assert isinstance(msgs, list) and msgs
        assert msgs[0]["role"] == "system"
        assert all("role" in m and "content" in m for m in msgs)
    # 真实性:取回的 == 当时真正喂给 LLM 的 build_messages 输出
    assert ctx["director_a"] == a_msgs
    assert ctx["writer"] == w_msgs
    assert ctx["director_b"] == b_msgs
    # 顺带复证注入隔离:A 的上下文含知识库哨兵,Writer/B 不含
    assert any("KB哨兵串" in m["content"] for m in ctx["director_a"])
    assert all("KB哨兵串" not in m["content"] for m in ctx["writer"])
    assert all("KB哨兵串" not in m["content"] for m in ctx["director_b"])


async def test_get_step_contexts_missing_turn_returns_none(tmp_path):
    Session = await _setup(tmp_path)
    async with Session() as s:
        assert await get_step_contexts(s, "st", 999) is None


async def test_prune_step_contexts_keeps_recent(tmp_path):
    """清理钩子:置空除最近 N 轮以外旧轮的三份 messages,保留 Turn 行其余字段。"""
    Session = await _setup(tmp_path)
    dummy = json.dumps([{"role": "system", "content": "x"}], ensure_ascii=False)
    async with Session() as s:
        for i in range(3):
            await reduce_turn(
                story_id="st", director_b_new_blackboard_str=_NEW_BB, writer_narrative=f"n{i}",
                director_a_json="{}", user_input=f"u{i}", session=s,
                director_a_messages=dummy, writer_messages=dummy, director_b_messages=dummy,
            )

    async with Session() as s:
        pruned = await prune_step_contexts(s, "st", keep_recent_n=1)
    assert pruned == 2  # 共 3 轮,保留最近 1 轮,清理 2 轮

    async with Session() as s:
        recent = await get_step_contexts(s, "st", 3)
        old = await get_step_contexts(s, "st", 1)
    assert recent["director_a"]  # 最近一轮保留
    assert old["director_a"] == [] and old["writer"] == [] and old["director_b"] == []  # 旧轮被清空
    # 清理只动 messages 列,Turn 行其余字段仍在(narrative 还在)
    async with Session() as s:
        from app.db.models import Turn
        from sqlalchemy import select
        t1 = (await s.execute(select(Turn).where(Turn.story_id == "st", Turn.turn_index == 1))).scalar_one()
    assert t1.narrative == "n0"
