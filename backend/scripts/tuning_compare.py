"""第四步:推进性调优的人工评判素材。

用用户给定的同一段输入(发现胶囊:凯撒实验品 / 主角是绑架目标 / 副作用不可逆缩小)
跑调优后的 Director-A → Writer,贴出:
  ① A 的完整输出 —— 看 beat_points 是否铺出一条推进弧线、字段顺序是否形成思考链;
  ② Writer 的叙事 —— 看是否从「围着发现打转」变成「有运动感地推进到下一个自然节点」。
并报告缓存 usage(确认未受影响)。无 assert,人工评判。

用法(backend/ 下):  python -m scripts.tuning_compare
"""

import asyncio
import json

from pydantic import ValidationError

from app.agents.context import build_messages
from app.agents.writer import stream_writer
from app.config import settings
from app.llm.deepseek_client import get_client
from app.models.schemas import DirectorOutput

# 复现用户给定的那一轮:夏莱职员的日常 → 到储物间(找纸盒这类过渡动作)→ 发现胶囊。
# 含「凯撒实验品 / 主角是绑架目标 / 胶囊副作用不可逆缩小」三项事实。
BLACKBOARD = {
    "story_meta": {"title": "缩小", "current_scene": "storage_room", "latest_beat": ""},
    "scenes": {
        "storage_room": {
            "name": "夏莱储物间",
            "base_prompt": "夏莱办公区里侧的储物间,一排排金属货架,堆着文件箱、备用器材和杂物",
            "visual_anchors": ["金属货架", "成摞的文件箱", "天花板的日光灯"],
            "state": "寻常的工作日下午,储物间没什么人,只有日光灯的微响",
            "connections": ["办公区", "走廊"],
            "image_paths": [],
        }
    },
    "characters": {
        "你": {"location": "storage_room", "status": "夏莱的普通职员,在过寻常的工作日;被某个绑架计划盯上(尚不自知)",
               "inventory": [], "relations": {"凯撒": "素未谋面,却是同一计划的两端"}, "appearance": "夏莱工作人员"},
        "凯撒": {"location": "未知", "status": "被当作实验品关押,与那批胶囊有关",
                 "inventory": [], "relations": {}, "appearance": "实验体编号'凯撒'"},
    },
    "items": {
        "胶囊": {"owner": "scene:storage_room", "where": "储物间最里侧货架的角落",
                 "desc": "几枚来历不明的胶囊,是'凯撒'实验的产物;一旦服用会导致身体不可逆地缩小"},
    },
    "notes": [
        {"content": "主角是某项绑架计划锁定的目标,本人尚不知情", "since_beat": "序"},
        {"content": "胶囊的副作用是不可逆的身体缩小", "since_beat": "序"},
    ],
}

# 这一段同时含「日常过渡(照常值班、到储物间找纸盒)」与「关键(发现胶囊)」,用来看 A 的详略判断
USER_ACTION = "今天照常值班,下午要打包一批资料,我到储物间想找几个空纸盒,却在最里侧货架的角落看见了几枚胶囊。"


async def run_director_capture(history, blackboard, user_action):
    """内联跑 Director-A,既拿解析后的输出、也拿 usage(看缓存)。"""
    client = get_client()
    messages = build_messages("director", history=history, blackboard=blackboard, user_action=user_action)
    resp = await client.chat.completions.create(
        model=settings.deepseek_model, messages=messages, temperature=0.3,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or ""
    out = DirectorOutput.model_validate(json.loads(raw))
    return out, raw, resp.usage


def print_usage(tag, usage):
    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    miss = getattr(usage, "prompt_cache_miss_tokens", None)
    print(f"  [{tag}] prompt_tokens={usage.prompt_tokens} cache_hit={hit} cache_miss={miss} completion={usage.completion_tokens}")


async def main() -> None:
    print(f"model={settings.deepseek_model}\n玩家行动:{USER_ACTION}\n")

    # 跑两次 Director-A:第一次暖缓存,第二次看稳定命中(确认改 schema 后缓存机制未坏)
    a1, _, u1 = await run_director_capture([], BLACKBOARD, USER_ACTION)
    a, raw, u2 = await run_director_capture([], BLACKBOARD, USER_ACTION)

    print("===== ① Director-A 完整输出(字段即生成顺序:确定→思考→总结)=====")
    d = a.model_dump()
    for k in ["situation", "scene_intent", "scene_hint", "beat_points", "mood", "pacing", "writing_brief", "choices"]:
        v = d.get(k)
        if isinstance(v, list):
            print(f"  {k}:")
            for i, item in enumerate(v, 1):
                print(f"      {i}. {item}")
        else:
            print(f"  {k}: {v}")

    print("\n  — 缓存 usage(本轮重写了文风圣经=新前缀:第1次必冷缺失,第2次起命中新基线)—")
    print_usage("A 第1次(暖)", u1)
    print_usage("A 第2次(稳定)", u2)

    print("\n===== ② Writer 叙事(看是否有运动感地推进,而非围着『发现』打转)=====")
    chunks = []
    async for tok in stream_writer([], BLACKBOARD, USER_ACTION, a.writing_brief):
        chunks.append(tok)
    narrative = "".join(chunks)
    print(narrative)
    print(f"\n[叙事字数 ≈ {len(narrative)}]")


if __name__ == "__main__":
    asyncio.run(main())
