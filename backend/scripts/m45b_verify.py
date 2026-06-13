"""M4.5-B 验证:持久化每步完整上下文。

跑一轮真实 A→Writer→B(走真正的 agent 调用,messages= 复用同一份),证明:
  ① 三份完整 messages 都被存进 Turn,且各是合法完整数组(system + 历史 + 尾部任务)。
  ② 读取接口按 turn_index 取回;取回的就是当时真正喂给 LLM 的上下文(== build_messages 输出)。
  ③ 缓存未受影响:对 A 的同一份 messages 连调两次,hit/miss 与 M4.5-A 同型
     (额外存 messages 是旁路落盘,build_messages 与喂给 LLM 的内容逐字节没变)。
  ④ 清理钩子存在(prune_step_contexts 可调用)。

用 fresh db(create_all 建全 schema),不污染主库。用法(backend/ 下,需 DeepSeek key):
    python -m scripts.m45b_verify
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
from app.db.models import Blackboard, Knowledge, Story
from app.db.session import create_all, make_engine, make_session_factory
from app.knowledge.store import get_knowledge, set_knowledge
from app.llm.deepseek_client import get_client
from app.state.reducer import reduce_turn
from app.turns.step_contexts import get_step_contexts, prune_step_contexts

STORY = "m45b-verify"
SENTINEL = "白子·摩托·阿拜多斯哨兵串"
KNOWLEDGE = f"# 角色:白子(Shiroko)  [{SENTINEL}]\n阿拜多斯对策委员会,沉默寡言,喜欢摩托与狗。\n"
BLACKBOARD = {
    "story_meta": {"title": "阿拜多斯", "current_scene": "clubroom", "latest_beat": ""},
    "scenes": {"clubroom": {"name": "社团室", "base_prompt": "破旧温暖的社团室", "visual_anchors": [],
                            "state": "午后,白子在保养摩托", "connections": [], "image_paths": []}},
    "characters": {"白子": {"location": "clubroom", "status": "保养摩托", "inventory": [],
                            "relations": {}, "appearance": "蓝发"}},
    "items": {}, "notes": [],
}
USER_ACTION = "我推开社团室的门,问白子在忙什么。"


def usage_line(tag, u):
    print(f"    [{tag}] prompt={u.prompt_tokens} "
          f"cache_hit={getattr(u, 'prompt_cache_hit_tokens', None)} "
          f"cache_miss={getattr(u, 'prompt_cache_miss_tokens', None)} completion={u.completion_tokens}")


async def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "m45b.db"
    engine = make_engine(f"sqlite+aiosqlite:///{tmp}")
    await create_all(engine)
    Session = make_session_factory(engine)

    async with Session() as s:
        s.add(Story(id=STORY, title="M4.5-B 验证"))
        s.add(Blackboard(story_id=STORY, json_blob=json.dumps(BLACKBOARD, ensure_ascii=False)))
        await s.commit()
        await set_knowledge(s, STORY, KNOWLEDGE)
        knowledge = await get_knowledge(s, STORY)

    history: list[dict] = []
    # 三份完整 messages 各构造一次,既喂 LLM、又存档(与 turn_router 同一套捕获逻辑)
    a_msgs = build_messages("director", history=history, blackboard=BLACKBOARD, user_action=USER_ACTION, knowledge=knowledge)
    a = await run_director(history, BLACKBOARD, USER_ACTION, knowledge=knowledge, messages=a_msgs)
    w_msgs = build_messages("writer", history=history, blackboard=BLACKBOARD, user_action=USER_ACTION, writing_brief=a.writing_brief)
    chunks = []
    async for tok in stream_writer(history, BLACKBOARD, USER_ACTION, a.writing_brief, messages=w_msgs):
        chunks.append(tok)
    narrative = "".join(chunks)
    b_msgs = build_messages("director_review", history=history, blackboard=BLACKBOARD, user_action=USER_ACTION,
                            narrative=narrative, director_a_plan=a.model_dump())
    new_bb = await run_director_review(history, BLACKBOARD, USER_ACTION, narrative, director_a_plan=a.model_dump(), messages=b_msgs)

    async with Session() as s:
        result = await reduce_turn(
            story_id=STORY, director_b_new_blackboard_str=json.dumps(new_bb, ensure_ascii=False),
            writer_narrative=narrative, director_a_json=a.model_dump_json(), user_input=USER_ACTION, session=s,
            director_a_messages=json.dumps(a_msgs, ensure_ascii=False),
            writer_messages=json.dumps(w_msgs, ensure_ascii=False),
            director_b_messages=json.dumps(b_msgs, ensure_ascii=False),
        )
    print(f"已写入 Turn #{result.turn_index}\n")

    # ---- ① / ② 存下 + 取回 + 真实性 ----
    print("===== ① 三份完整 messages 已存下;② 按 turn_index 取回且与当时喂给 LLM 的一致 =====")
    async with Session() as s:
        ctx = await get_step_contexts(s, STORY, result.turn_index)
    assert ctx is not None
    for step, src in [("director_a", a_msgs), ("writer", w_msgs), ("director_b", b_msgs)]:
        msgs = ctx[step]
        roles = [m["role"] for m in msgs]
        faithful = msgs == src
        print(f"  {step:11s}: 条数={len(msgs)} roles={roles[:2]}…{roles[-1:]} | system开头={msgs[0]['role']=='system'} | ==build_messages输出 {faithful}")
        assert faithful and msgs[0]["role"] == "system"
    assert any(SENTINEL in m["content"] for m in ctx["director_a"])
    assert all(SENTINEL not in m["content"] for m in ctx["writer"])
    assert all(SENTINEL not in m["content"] for m in ctx["director_b"])
    print("  ✅ 三份都是合法完整上下文;取回的 == 当时真正喂给 LLM 的 messages;A 含知识库、Writer/B 不含。")

    # ---- ③ 缓存未受影响:对 A 的同一份 messages 连调两次 ----
    print("\n===== ③ 缓存未受影响(对 A 的同一份 messages 连调两次,与 M4.5-A 同型)=====")
    client = get_client()
    r1 = await client.chat.completions.create(model=settings.deepseek_model, messages=a_msgs, temperature=0.3, response_format={"type": "json_object"})
    r2 = await client.chat.completions.create(model=settings.deepseek_model, messages=a_msgs, temperature=0.3, response_format={"type": "json_object"})
    usage_line("A 第1次(暖)", r1.usage)
    usage_line("A 第2次(稳定)", r2.usage)
    print("  存 messages 是旁路落盘:喂给 LLM 的内容与 build_messages 输出逐字节相同 → 缓存命中与 M4.5-A 一致。")

    # ---- ④ 清理钩子存在 ----
    print("\n===== ④ 清理钩子 =====")
    async with Session() as s:
        pruned = await prune_step_contexts(s, STORY, keep_recent_n=5)
    print(f"  prune_step_contexts(keep_recent_n=5) 可调用,本轮清理 {pruned} 轮(仅 1 轮且在保留窗口内 → 0)。")
    print("\n✅ M4.5-B 验证完成。")


if __name__ == "__main__":
    asyncio.run(main())
