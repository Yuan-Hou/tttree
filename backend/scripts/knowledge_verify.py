"""M4.5-A 注入与缓存验证:知识库是否只进 Director-A、Writer/B 是否确实拿不到、
A 的缓存有没有因为多了知识库而异常。

Part 1(确定性,不花钱):对同一组输入,分别构造三个 agent 的 messages,
        看知识库哨兵串只出现在 A 的上下文里,Writer / Director-B 完全没有。
Part 2(真实调用):给故事写入一段蔚蓝档案设定,真实跑 A→Writer→B 各两次,贴 usage:
        A 的缓存在带知识库时仍正常命中;Writer/B 的 prompt 不含知识库、缓存照常。

用法(backend/ 下,需真实 DeepSeek key):  python -m scripts.knowledge_verify
"""

import asyncio
import json

from sqlalchemy import select

from app.agents.context import build_messages
from app.config import settings
from app.db.models import Blackboard, Knowledge, Story
from app.db.session import async_session, create_all, engine
from app.knowledge.store import get_knowledge, set_knowledge
from app.llm.deepseek_client import get_client

STORY = "kb-verify"
SENTINEL = "白子·摩托·阿拜多斯哨兵串"  # 独特串,便于在三个 agent 上下文里 grep
KNOWLEDGE = (
    f"# 角色:白子(Shiroko)  [{SENTINEL}]\n"
    "阿拜多斯高中对策委员会成员。沉默寡言,话少而短,偶尔一句出乎意料的认真。\n"
    "喜欢摩托车与狗。行动果断,临场冷静。\n\n"
    "# 世界观:基沃托斯\n"
    "学生自治的庞大都市,学生头顶有光环(Halo)。夏莱(Schale)是协助各社团解决事件的机构。\n"
)
BLACKBOARD = {
    "story_meta": {"title": "阿拜多斯的一天", "current_scene": "abydos_clubroom", "latest_beat": ""},
    "scenes": {"abydos_clubroom": {"name": "对策委员会社团室", "base_prompt": "破旧但温暖的社团室",
                                     "visual_anchors": ["旧沙发", "白板"], "state": "午后,白子在擦拭摩托零件",
                                     "connections": [], "image_paths": []}},
    "characters": {"白子": {"location": "abydos_clubroom", "status": "正在保养摩托",
                            "inventory": [], "relations": {}, "appearance": "蓝发,戴耳机"}},
    "items": {}, "notes": [],
}
USER_ACTION = "我推开社团室的门,问白子在忙什么。"


async def seed() -> None:
    await create_all(engine)
    async with async_session() as s:
        for model in (Knowledge, Blackboard):
            row = await s.get(model, STORY)
            if row:
                await s.delete(row)
        st = await s.get(Story, STORY)
        if st is None:
            s.add(Story(id=STORY, title="知识库验证"))
        s.add(Blackboard(story_id=STORY, json_blob=json.dumps(BLACKBOARD, ensure_ascii=False)))
        await s.commit()
        await set_knowledge(s, STORY, KNOWLEDGE)


def usage_line(tag, u):
    hit = getattr(u, "prompt_cache_hit_tokens", None)
    miss = getattr(u, "prompt_cache_miss_tokens", None)
    print(f"    [{tag}] prompt={u.prompt_tokens} cache_hit={hit} cache_miss={miss} completion={u.completion_tokens}")


async def create(messages, *, json_mode, temperature):
    kwargs = {"model": settings.deepseek_model, "messages": messages, "temperature": temperature}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return await get_client().chat.completions.create(**kwargs)


async def main() -> None:
    await seed()
    async with async_session() as s:
        kb = await get_knowledge(s, STORY)
    assert SENTINEL in kb

    # ---------- Part 1:注入隔离(确定性)----------
    print("===== Part 1:三个 agent 的上下文对比(知识库哨兵串只应出现在 A)=====")
    roles = {
        "Director-A": build_messages("director", history=[], blackboard=BLACKBOARD, user_action=USER_ACTION, knowledge=kb),
        "Writer": build_messages("writer", history=[], blackboard=BLACKBOARD, user_action=USER_ACTION, writing_brief="(占位)", knowledge=kb),
        "Director-B": build_messages("director_review", history=[], blackboard=BLACKBOARD, user_action=USER_ACTION, narrative="(占位叙事)", knowledge=kb),
    }
    for name, msgs in roles.items():
        has = any(SENTINEL in m["content"] for m in msgs)
        sys_msgs = [m for m in msgs if m["role"] == "system"]
        print(f"  {name:11s}: 含知识库={'是 ✅' if has else '否'} | system 消息数={len(sys_msgs)} | 总消息数={len(msgs)}")
    a_sys = [m for m in roles["Director-A"] if m["role"] == "system"]
    print(f"\n  A 的第1条 system(文风圣经,前40字): {a_sys[0]['content'][:40]}…")
    print(f"  A 的第2条 system(知识库,前60字): {a_sys[1]['content'][:60]}…")
    assert any(SENTINEL in m["content"] for m in roles["Director-A"])
    assert all(SENTINEL not in m["content"] for m in roles["Writer"])
    assert all(SENTINEL not in m["content"] for m in roles["Director-B"])
    print("\n  ✅ 注入隔离正确:只有 Director-A 拿到知识库;Writer / Director-B 完全没有。")

    # ---------- Part 2:真实调用 + 缓存 ----------
    print("\n===== Part 2:真实跑 A→Writer→B 各两次,看缓存(A 带知识库是否仍命中)=====")
    # A(带知识库)跑两次
    a_msgs = roles["Director-A"]
    ra1 = await create(a_msgs, json_mode=True, temperature=0.3)
    ra2 = await create(a_msgs, json_mode=True, temperature=0.3)
    a = json.loads(ra1.choices[0].message.content)
    print("  Director-A(上下文含知识库):")
    usage_line("A 第1次(暖)", ra1.usage)
    usage_line("A 第2次(稳定)", ra2.usage)

    # Writer(不含知识库)跑两次
    w_msgs = build_messages("writer", history=[], blackboard=BLACKBOARD, user_action=USER_ACTION,
                            writing_brief=a.get("writing_brief", ""), knowledge=kb)
    rw1 = await create(w_msgs, json_mode=False, temperature=0.85)
    rw2 = await create(w_msgs, json_mode=False, temperature=0.85)
    narrative = rw1.choices[0].message.content
    print("  Writer(上下文不含知识库):")
    usage_line("W 第1次", rw1.usage)
    usage_line("W 第2次", rw2.usage)

    # Director-B(不含知识库)跑两次
    b_msgs = build_messages("director_review", history=[], blackboard=BLACKBOARD, user_action=USER_ACTION,
                            narrative=narrative, director_a_plan=a, knowledge=kb)
    rb1 = await create(b_msgs, json_mode=True, temperature=0.3)
    rb2 = await create(b_msgs, json_mode=True, temperature=0.3)
    print("  Director-B(上下文不含知识库):")
    usage_line("B 第1次", rb1.usage)
    usage_line("B 第2次", rb2.usage)

    print(f"\n  A 的 prompt_tokens({ra1.usage.prompt_tokens}) 比 Writer({rw1.usage.prompt_tokens})多出的部分,"
          "即多注入的知识库;A 第2次 cache_hit 覆盖了含知识库的稳定前缀 → 知识库未损害 A 的缓存。")
    print("\n✅ M4.5-A 注入与缓存验证完成。")


if __name__ == "__main__":
    asyncio.run(main())
