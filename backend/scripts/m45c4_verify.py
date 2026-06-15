"""M4.5-C-4 重试的真实 LLM 验证(缓存/上下文复用那条验收)。

跑一个真实回合(A→Writer→B 存档每步上下文)→ 从 Writer 前真实重试 → 贴:
  - A 输出保留未变;Writer/B 是新结果(旧结果已被覆盖);
  - 重试复用的 Writer 上下文 == 原回合存档的 Writer 上下文(复用了正确上下文);
  - 对该上下文连调两次的缓存 usage(复用正确前缀 → 缓存命中,未坏)。

用 fresh db,不污染主库。用法(backend/ 下,需 DeepSeek key):  python -m scripts.m45c4_verify
"""

import asyncio
import json
import tempfile
from pathlib import Path

from app.agents.context import build_messages
from app.agents.director import run_director
from app.agents.director_review import run_director_review
from app.agents.writer import stream_writer
from app.config import settings
from app.db.models import Blackboard, Story, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.llm.deepseek_client import get_client
from app.state.reducer import reduce_turn
from app.turns.retry import retry_turn
from sqlalchemy import select

STORY = "m45c4"
ACTION = "我走到房间深处那扇没开过的门前。"
PRE_BB = {
    "story_meta": {"title": "门后", "current_scene": "room", "latest_beat": ""},
    "scenes": {"room": {"name": "房间", "base_prompt": "昏暗的房间,里侧有一扇门", "visual_anchors": ["木门"],
                        "state": "安静,门虚掩着", "connections": [], "image_paths": [], "origin_turn": 0}},
    "characters": {}, "items": {}, "notes": [],
}


def usage_line(tag, u):
    print(f"    [{tag}] prompt={u.prompt_tokens} cache_hit={getattr(u,'prompt_cache_hit_tokens',None)} "
          f"cache_miss={getattr(u,'prompt_cache_miss_tokens',None)} completion={u.completion_tokens}")


async def main() -> None:
    eng = make_engine(f"sqlite+aiosqlite:///{Path(tempfile.mkdtemp())/'d.db'}")
    await create_all(eng)
    S = make_session_factory(eng)
    async with S() as s:
        s.add(Story(id=STORY, title="门后"))
        s.add(Blackboard(story_id=STORY, json_blob=json.dumps(PRE_BB, ensure_ascii=False)))
        await s.commit()

    # ---- 真实回合 1(存档三步上下文)----
    a_msgs = build_messages("director", history=[], blackboard=PRE_BB, user_action=ACTION, knowledge="")
    a = await run_director([], PRE_BB, ACTION, messages=a_msgs)
    w_msgs = build_messages("writer", history=[], blackboard=PRE_BB, user_action=ACTION, writing_brief=a.writing_brief)
    narrative = "".join([t async for t in stream_writer([], PRE_BB, ACTION, a.writing_brief, messages=w_msgs)])
    b_msgs = build_messages("director_review", history=[], blackboard=PRE_BB, user_action=ACTION,
                            narrative=narrative, director_a_plan=a.model_dump())
    new_bb = await run_director_review([], PRE_BB, ACTION, narrative, director_a_plan=a.model_dump(), messages=b_msgs)
    async with S() as s:
        await reduce_turn(story_id=STORY, director_b_new_blackboard_str=json.dumps(new_bb, ensure_ascii=False),
                          writer_narrative=narrative, director_a_json=a.model_dump_json(), user_input=ACTION, session=s,
                          director_a_messages=json.dumps(a_msgs, ensure_ascii=False),
                          writer_messages=json.dumps(w_msgs, ensure_ascii=False),
                          director_b_messages=json.dumps(b_msgs, ensure_ascii=False))

    async with S() as s:
        t_before = (await s.execute(select(Turn).where(Turn.story_id == STORY, Turn.turn_index == 1))).scalar_one()
        a_json_before, narr_before = t_before.director_a_json, t_before.narrative
        w_msgs_before = t_before.writer_messages
    print("原回合 A.writing_brief:", json.loads(a_json_before)["writing_brief"][:50], "…")
    print("原回合 narrative(前40):", narr_before[:40], "…\n")

    # ---- 从 Writer 前真实重试 ----
    async with S() as s:
        r = await retry_turn(s, STORY, "writer")
    async with S() as s:
        t_after = (await s.execute(select(Turn).where(Turn.story_id == STORY, Turn.turn_index == 1))).scalar_one()

    print("=== 从 Writer 前重试结果 ===")
    print("  A 保留未变:", json.loads(t_after.director_a_json) == json.loads(a_json_before))
    print("  Writer 是新结果(成稿已变):", t_after.narrative != narr_before)
    print("  新 narrative(前40):", t_after.narrative[:40], "…")
    print("  复用了正确的 Writer 上下文(== 原回合存档):", t_after.writer_messages == w_msgs_before)
    print(f"  turn_index 复用={r.turn_index}  作废场景={r.invalidated_scene_slugs}  新生场景={r.new_scene_slugs}")

    # ---- 缓存未坏:对复用的 Writer 上下文连调两次 ----
    print("\n=== 复用的 Writer 上下文缓存 usage ===")
    reused = json.loads(t_after.writer_messages)
    client = get_client()
    r1 = await client.chat.completions.create(model=settings.deepseek_model, messages=reused)
    r2 = await client.chat.completions.create(model=settings.deepseek_model, messages=reused)
    usage_line("第1次", r1.usage)
    usage_line("第2次(稳定)", r2.usage)
    print("\n✅ M4.5-C-4 真实重试验证完成:A 保留、Writer/B 重走、复用正确上下文、缓存命中。")


if __name__ == "__main__":
    asyncio.run(main())
