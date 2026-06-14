"""绘图提案持久化(M5-B 绘图语义升级·子步一)。

驱动 reduce_turn 跑几轮(crafted B 输出,无 LLM),断言 DrawProposal 表里跨轮积压、
kind 按 origin_turn 权威判定(故意喂 B 错误的 kind 以证明后端覆盖)、回退清理依附轮的提案。
"""

import json

import pytest
from sqlalchemy import select

from app.db.models import DrawProposal
from app.db.session import create_all, make_engine, make_session_factory
from app.state.reducer import reduce_turn
from app.stories.store import create_story
from app.turns.rollback import rollback_latest_turn


def _b_out(scenes: dict, proposals: list[dict]) -> str:
    return json.dumps(
        {
            "story_meta": {"title": "T", "current_scene": next(iter(scenes), ""), "latest_beat": "拍"},
            "scenes": scenes,  # 不带 origin_turn;reducer 权威打点
            "characters": {},
            "items": {},
            "notes": [],
            "draw_proposals": proposals,
        },
        ensure_ascii=False,
    )


async def _reduce(Session, sid, scenes, proposals):
    async with Session() as s:
        return await reduce_turn(
            story_id=sid,
            director_b_new_blackboard_str=_b_out(scenes, proposals),
            writer_narrative="叙事",
            director_a_json="{}",
            user_input="行动",
            session=s,
        )


@pytest.fixture
async def Session(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'dp.db'}")
    await create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


SC = {"name": "场景", "state": "", "image_paths": []}


async def test_proposals_persist_kind_by_origin_turn_across_turns(Session):
    async with Session() as s:
        sid = (await create_story(s, title="T")).id

    # turn1: 只有 intro,无提案
    await _reduce(Session, sid, {"intro": dict(SC)}, [])
    # turn2: 场景 X 诞生 + 提案 X(故意喂 B 的 kind=variant → 后端应按 origin_turn=2 覆盖成 new_scene)
    await _reduce(Session, sid, {"intro": dict(SC), "X": dict(SC)},
                  [{"scene_slug": "X", "kind": "variant", "reason": "r2"}])
    # turn3: 召回 X + 提案 X(故意喂 B 的 kind=new_scene → 后端应覆盖成 variant)
    await _reduce(Session, sid, {"intro": dict(SC), "X": dict(SC)},
                  [{"scene_slug": "X", "kind": "new_scene", "reason": "r3"}])
    # turn4: 无提案
    await _reduce(Session, sid, {"intro": dict(SC), "X": dict(SC)}, [])
    # turn5: 又提案 X
    await _reduce(Session, sid, {"intro": dict(SC), "X": dict(SC)},
                  [{"scene_slug": "X", "kind": "variant", "reason": "r5"}])

    async with Session() as s:
        rows = (
            await s.execute(select(DrawProposal).where(DrawProposal.story_id == sid))
        ).scalars().all()
    xs = sorted((r.origin_proposal_turn, r.kind, r.status) for r in rows if r.scene_slug == "X")
    # X 诞生于第2轮 → 仅第2轮那条 new_scene,第3/5轮都是 variant;跨轮都 pending
    assert xs == [(2, "new_scene", "pending"), (3, "variant", "pending"), (5, "variant", "pending")]


async def test_rollback_clears_only_that_turns_proposals(Session):
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await _reduce(Session, sid, {"X": dict(SC)}, [{"scene_slug": "X", "reason": "r1"}])  # turn1
    await _reduce(Session, sid, {"X": dict(SC)}, [{"scene_slug": "X", "reason": "r2"}])  # turn2

    async with Session() as s:
        await rollback_latest_turn(s, sid)  # 回退 turn2

    async with Session() as s:
        rows = (
            await s.execute(select(DrawProposal).where(DrawProposal.story_id == sid))
        ).scalars().all()
    assert sorted(r.origin_proposal_turn for r in rows) == [1]  # turn2 的提案清掉,turn1 留存
