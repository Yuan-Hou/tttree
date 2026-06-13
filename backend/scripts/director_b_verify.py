"""Director-B 重构验收:时间语义 + 跨多场景登记 + recall 复用 slug + 消费 A 意图(非权威)。
构造 Writer 成稿喂真实 Director-B,断言新黑板。真实 DeepSeek、零 gpt-image-2。
用法(backend/ 下):python -m scripts.director_b_verify
"""

import asyncio
import copy
import json

from app.agents.director_review import run_director_review


def check(label: str, ok: bool) -> None:
    print(f"  {'✅' if ok else '❌'} {label}")


def one_char(bb: dict):
    chars = bb.get("characters", {})
    name = next(iter(chars), None)
    return name, chars.get(name, {})


# ============ 场景 1:一轮跨多场景(甲→乙→丙,停在丙)============
BB1 = {
    "story_meta": {"title": "沉睡之舰", "current_scene": "corridor", "latest_beat": "醒于走廊"},
    "scenes": {
        "corridor": {
            "name": "狭长走廊", "base_prompt": "昏暗狭长的金属走廊,管线裸露",
            "visual_anchors": ["裸露管线", "忽明忽暗的应急灯"],
            "state": "应急灯忽明忽暗,尽头连着一道门。", "connections": [], "image_paths": [],
        }
    },
    "characters": {"我": {"location": "corridor", "status": "刚醒,虚弱", "inventory": [], "relations": {}, "appearance": "一身褪色制服的旅人"}},
    "items": {}, "notes": [],
}
NARR1 = (
    "你扶着冰冷的管壁,沿走廊向前。第一道门后是一间控制室——环形操作台围在中央,大半屏幕已经碎裂,"
    "只剩一块还固执地闪着红光。你没有停留,穿过控制室,推开里侧那扇厚重的舱门。"
    "门后是一间休眠舱室:一排排透明的低温舱沿墙排列,大多空着,只有最深处一具舱体仍泛着幽蓝的光。"
    "你在休眠舱室里停下脚步,盯着那具还亮着的舱体。"
)
A_PLAN1 = {"situation": "主角站在幽暗走廊口,准备沿走廊往深处推进。",
           "beat_points": ["沿走廊深入", "穿过控制室", "抵达休眠舱室并停在亮着的舱体前"],
           "mood": "幽闭不安",
           "scene_intent": "likely_new_scene", "scene_hint": "走廊尽头似乎通向更深的舱室",
           "writing_brief": "第二人称、幽闭压抑的笔触,随主角推进逐一揭示控制室与休眠舱室的细节;停在最深处亮着的舱体。"}


async def scenario_1():
    print("\n" + "=" * 72 + "\n场景1:一轮跨多场景(走廊→控制室→休眠舱室,停在休眠舱室)\n" + "=" * 72)
    bb = await run_director_review([], copy.deepcopy(BB1), "沿走廊一直往里走", NARR1, director_a_plan=A_PLAN1)
    scenes = bb.get("scenes", {})
    cur = bb.get("story_meta", {}).get("current_scene")
    cname, c = one_char(bb)
    print("scenes:", {k: v.get("name") for k, v in scenes.items()}, "| current_scene:", cur)
    check("走廊(起点)仍在 scenes", "corridor" in scenes)
    check(f"登记了多个场景(实得 {len(scenes)} 个 >= 3)", len(scenes) >= 3)
    check(f"current_scene 是最终停留场景(非起点 corridor,实得 {cur!r})", cur not in (None, "corridor"))
    check(f"current_scene 指向存在的场景 + 主角在那里(loc={c.get('location')!r})", cur in scenes and c.get("location") == cur)
    check("每个场景 state 都非空(反映结束时刻)", all(s.get("state") for s in scenes.values()))
    check(f"场景数没有爆炸(<=4,未把纯过渡误建成场景,实得 {len(scenes)})", len(scenes) <= 4)


# ============ 场景 2:recall 复用已有 slug(离开地窖→回到地窖)============
BB2 = {
    "story_meta": {"title": "地窖之谜", "current_scene": "stairwell", "latest_beat": "离开地窖"},
    "scenes": {
        "cellar": {
            "name": "潮湿地窖", "base_prompt": "低矮潮湿的石砌地窖", "visual_anchors": ["渗水石墙", "旧木架"],
            "state": "石墙渗水,角落木架上摆着一盏熄灭的油灯;地面有半圈未干的脚印。",
            "connections": ["stairwell"], "image_paths": ["storage/images/cellar_seed.png"],
        },
        "stairwell": {
            "name": "狭窄楼梯", "base_prompt": "通向地窖的狭窄石阶", "visual_anchors": ["石阶"],
            "state": "石阶向上延伸,顶端透进微光。", "connections": ["cellar"], "image_paths": [],
        },
    },
    "characters": {"我": {"location": "stairwell", "status": "警觉", "inventory": [], "relations": {}, "appearance": "提灯的探险者"}},
    "items": {}, "notes": [],
}
NARR2 = (
    "你转身走下石阶,重新回到那间潮湿的地窖。一切如你离开时——渗水的石墙,角落木架上那盏熄灭的油灯,"
    "地上还留着你先前的脚印。你站定,环顾这片熟悉的低矮空间,没有动任何东西。"
)
A_PLAN2 = {"situation": "主角站在石阶上方,打算沿石阶返回先前那间地窖。",
           "beat_points": ["沿石阶往下走", "回到熟悉的地窖,环视一切如旧"],
           "mood": "熟悉而警觉", "scene_intent": "likely_recall",
           "scene_hint": "回到之前那间潮湿的地窖", "writing_brief": "第二人称,回到熟悉地窖,强调'一切如旧'的静止感。"}


async def scenario_2():
    print("\n" + "=" * 72 + "\n场景2:recall 复用已有 slug(回到地窖,不新建重复场景)\n" + "=" * 72)
    bb = await run_director_review([], copy.deepcopy(BB2), "走下楼梯回到地窖", NARR2, director_a_plan=A_PLAN2)
    scenes = bb.get("scenes", {})
    cur = bb.get("story_meta", {}).get("current_scene")
    cname, c = one_char(bb)
    print("scenes keys:", list(scenes), "| current_scene:", cur)
    check(f"current_scene 指回已有的 cellar slug(实得 {cur!r})", cur == "cellar")
    check(f"未新建重复场景(scenes 键集合不变,实得 {set(scenes)})", set(scenes) == {"cellar", "stairwell"})
    check("地窖已有 image_paths 被保留", scenes.get("cellar", {}).get("image_paths") == ["storage/images/cellar_seed.png"])
    check("地窖已有 state 被保留(未变化时,油灯细节仍在)", "油灯" in scenes.get("cellar", {}).get("state", ""))
    check(f"主角 location 指回 cellar(实得 {c.get('location')!r})", c.get("location") == "cellar")


# ============ 场景 3:A 猜 likely_recall 但 Writer 写了新场景 → B 以 Writer 为准 ============
BB3 = {
    "story_meta": {"title": "迷宫", "current_scene": "entrance_hall", "latest_beat": "入口大厅"},
    "scenes": {
        "entrance_hall": {
            "name": "入口大厅", "base_prompt": "宽阔空旷的入口大厅,四面立柱", "visual_anchors": ["高大立柱"],
            "state": "空旷,回声不绝,四面各有一道门。", "connections": [], "image_paths": [],
        }
    },
    "characters": {"我": {"location": "entrance_hall", "status": "探索中", "inventory": [], "relations": {}, "appearance": "持火把的人"}},
    "items": {}, "notes": [],
}
NARR3 = (
    "你推开西侧那扇从未开过的门。门后不是任何你来过的地方——一座向下盘旋的螺旋石梯,"
    "墙壁上爬满了幽幽发光的菌丝,空气里浮动着潮湿泥土的腥气。你踏上石梯,一步步走进这片从未见过的幽深。"
)
# A 误判为 recall(回入口大厅),但 Writer 明确写了一个全新场景
A_PLAN3 = {"situation": "主角站在西侧那扇从未开过的门前,准备推门看看门后是什么。",
           "beat_points": ["推开西侧的门", "看清门后的空间(A 猜也许又绕回入口)"],
           "mood": "未知", "scene_intent": "likely_recall",
           "scene_hint": "也许又回到了入口大厅", "writing_brief": "第二人称,推门后描写门后空间。"}


async def scenario_3():
    print("\n" + "=" * 72 + "\n场景3:A 猜 likely_recall,但 Writer 写了新场景 → B 应以 Writer 为准(新建)\n" + "=" * 72)
    bb = await run_director_review([], copy.deepcopy(BB3), "推开西侧那扇没开过的门", NARR3, director_a_plan=A_PLAN3)
    scenes = bb.get("scenes", {})
    cur = bb.get("story_meta", {}).get("current_scene")
    cname, c = one_char(bb)
    print("scenes:", {k: v.get("name") for k, v in scenes.items()}, "| current_scene:", cur)
    check("入口大厅仍在 scenes", "entrance_hall" in scenes)
    check(f"B 未盲从 A 的 likely_recall:current_scene 是新场景(非 entrance_hall,实得 {cur!r})", cur not in (None, "entrance_hall"))
    check(f"确实新建了场景(scenes 数 {len(scenes)} >= 2)", len(scenes) >= 2)
    check(f"主角在新场景里(loc={c.get('location')!r} == current)", c.get("location") == cur)


async def main():
    await scenario_1()
    await scenario_2()
    await scenario_3()


if __name__ == "__main__":
    asyncio.run(main())
