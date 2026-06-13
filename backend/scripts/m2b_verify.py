"""M2-B 验收:用构造的 Writer 成稿喂 Director-B,检验全量重写黑板的保真度。

覆盖:即兴新物品、实际进了新场景(与 A 预案不符)、物品被角色拾取(owner 变更)、
未变更部分逐字保留、value 中文标点、未被 prompt 说明文字污染。
真实调用 DeepSeek。用法(backend/ 下):python -m scripts.m2b_verify
"""

import asyncio
import copy
import json

from app.agents.director_review import run_director_review

# 当前黑板(本回合之前):森林空地,旅人空手,树桩上有一把铜钥匙,北侧有虚掩木门
BLACKBOARD = {
    "story_meta": {"title": "林间迷踪", "current_scene": "forest_edge", "latest_beat": "初入林间"},
    "scenes": {
        "forest_edge": {
            "name": "森林边缘的空地",
            "base_prompt": "黄昏时分的森林空地,四周古树参天,地面覆满落叶。",
            "visual_anchors": ["参天古树", "金色夕照", "厚厚的落叶"],
            "state": "宁静无人;空地中央有一截被砍倒的树桩,树桩上搁着一把铜钥匙;北侧立着一扇虚掩的木门。",
            "connections": ["hidden_cabin"],
            "image_paths": [],
        }
    },
    "characters": {
        "旅人": {
            "location": "forest_edge",
            "status": "清醒、警觉,因失忆而困惑",
            "inventory": [],
            "relations": {},
            "appearance": "一身风尘仆仆的灰色旅人斗篷,腰间空无一物。",
        }
    },
    "items": {
        "铜钥匙": {
            "owner": "scene:forest_edge",
            "where": "空地中央的树桩上",
            "desc": "一把样式古旧的铜钥匙,齿纹间嵌着暗绿的铜锈。",
        }
    },
    "notes": [{"content": "旅人失忆,不知自己为何身处此地。", "since_beat": "初入林间"}],
}

# Director-A 预案:A 误判旅人会「留在原地」拾起钥匙(stay) —— 与 Writer 实际走向不符
A_PLAN = {
    "beat": "旅人拾起树桩上的铜钥匙,端详片刻。",
    "mood": "安静的好奇",
    "scene_intent": "stay",
    "scene_hint": "",
    "writing_brief": "第二人称、安静好奇的笔触,聚焦钥匙的触感;须落实拾起铜钥匙;篇幅中。",
}

# 场景 1 成稿:拾钥匙(owner 变更)+ 即兴掏出一张褪色信笺(新物品)+ 实际推门进了木屋(新场景,与 A 的 stay 冲突)
NARRATIVE_1 = (
    "你俯身拾起树桩上的铜钥匙,金属的凉意透过指尖一路爬上手腕。"
    "钥匙原先压着的树皮缝里,露出一角褪色的信笺,边缘已经脆得发黄;你小心地把它抽出来,一并收进怀里。\n"
    "握着钥匙,你走向那扇虚掩的木门,抬手推开。门轴发出沉闷的吱呀。"
    "门后是一间狭小的木屋,空气里浮动着尘埃与旧木的气味,屋角堆着几只蒙尘的木箱,一张缺了腿的木桌斜倚在墙边。"
)

# 场景 2 成稿:纯氛围,无任何结构性变化(不移动、不拾取、不引入新实体)——验证逐字保留 + removed 空 + 不编造
NARRATIVE_2 = (
    "你站在原地,听风穿过头顶的树冠,落叶在脚边打着旋。"
    "夕照一寸寸沉下去,空地的影子被拉得很长。你只是静静站着,没有去碰那把钥匙,也没有靠近那扇门。"
)

SCHEMA_DESC_PHRASES = [
    "用于绘图的场景基底描述",
    "该场景固定不变的视觉特征",
    "本回合的小标题",
    "该角色当前所在场景的 slug",
    "需要后续呼应的伏笔",
    "占位记号",
]


def check(label: str, ok: bool) -> None:
    print(f"  {'✅' if ok else '❌'} {label}")


def no_schema_pollution(bb: dict) -> bool:
    blob = json.dumps(bb, ensure_ascii=False)
    return not any(p in blob for p in SCHEMA_DESC_PHRASES)


async def scenario_1() -> None:
    print("\n" + "=" * 70)
    print("场景 1:拾钥匙(owner 变更)+ 即兴新物品 + 实际进新场景(修正 A 的 stay)")
    print("=" * 70)
    new_bb = await run_director_review(
        history=[],
        blackboard=copy.deepcopy(BLACKBOARD),
        user_action="拾起钥匙,然后推开那扇木门",
        narrative=NARRATIVE_1,
        director_a_plan=A_PLAN,
    )
    print("\n--- Director-B 新黑板 ---")
    print(json.dumps(new_bb, ensure_ascii=False, indent=2))
    print("--- 断言 ---")

    cur = new_bb.get("story_meta", {}).get("current_scene")
    scenes = new_bb.get("scenes", {})
    chars = new_bb.get("characters", {})
    items = new_bb.get("items", {})

    check(f"current_scene 已被修正,不再是 forest_edge(实得 : {cur!r})", cur not in (None, "forest_edge"))
    check(f"current_scene 指向 scenes 中存在的键({cur!r} in scenes)", cur in scenes)
    check("forest_edge 场景仍保留(未被无故删除)", "forest_edge" in scenes)

    trav = chars.get("旅人", {})
    check(f"旅人 location == current_scene(实得 : {trav.get('location')!r})", trav.get("location") == cur)

    key = items.get("铜钥匙", {})
    check(f"铜钥匙 owner 变更为 character:旅人(实得 : {key.get('owner')!r})", key.get("owner") == "character:旅人")
    check("铜钥匙 出现在旅人 inventory", "铜钥匙" in trav.get("inventory", []))

    new_items = [k for k in items if "信" in k]
    check(f"即兴新物品(含『信』的物品)已登记(实得 : {new_items})", len(new_items) >= 1)

    # 逐字保留:forest_edge 的 base_prompt / visual_anchors / name 未被改写
    fe = scenes.get("forest_edge", {})
    orig_fe = BLACKBOARD["scenes"]["forest_edge"]
    check("forest_edge.base_prompt 逐字保留", fe.get("base_prompt") == orig_fe["base_prompt"])
    check("forest_edge.visual_anchors 逐字保留", fe.get("visual_anchors") == orig_fe["visual_anchors"])
    check("story_meta.title 逐字保留", new_bb.get("story_meta", {}).get("title") == "林间迷踪")
    check("失忆 note 仍在", any("失忆" in n.get("content", "") for n in new_bb.get("notes", [])))

    beat = new_bb.get("story_meta", {}).get("latest_beat", "")
    check(f"latest_beat 短小标签(实得 : {beat!r},长度 {len(beat)})", 0 < len(beat) <= 12 and "。" not in beat)
    check(f"removed 为数组(实得 : {new_bb.get('removed')!r})", isinstance(new_bb.get("removed"), list))
    check("未被 prompt 说明文字污染(无 schema 职责描述泄漏为 value)", no_schema_pollution(new_bb))
    check("value 含中文标点(、,。;)", any(p in json.dumps(new_bb, ensure_ascii=False) for p in "、，。;"))


async def scenario_2() -> None:
    print("\n" + "=" * 70)
    print("场景 2:纯氛围、零结构变化 —— 验证逐字保留 + removed 空 + 不编造实体")
    print("=" * 70)
    new_bb = await run_director_review(
        history=[],
        blackboard=copy.deepcopy(BLACKBOARD),
        user_action="环顾四周,什么也不碰",
        narrative=NARRATIVE_2,
        director_a_plan=A_PLAN,
    )
    print("\n--- Director-B 新黑板 ---")
    print(json.dumps(new_bb, ensure_ascii=False, indent=2))
    print("--- 断言 ---")

    check("scenes 键集合不变(未编造新场景)", set(new_bb.get("scenes", {})) == set(BLACKBOARD["scenes"]))
    check("characters 键集合不变(未编造新角色)", set(new_bb.get("characters", {})) == set(BLACKBOARD["characters"]))
    check("items 键集合不变(未编造新物品)", set(new_bb.get("items", {})) == set(BLACKBOARD["items"]))
    check(
        f"current_scene 仍为 forest_edge(实得 : {new_bb.get('story_meta', {}).get('current_scene')!r})",
        new_bb.get("story_meta", {}).get("current_scene") == "forest_edge",
    )
    check("铜钥匙 owner 未变(仍属场景)", new_bb.get("items", {}).get("铜钥匙", {}).get("owner") == "scene:forest_edge")
    check(f"removed 为空(实得 : {new_bb.get('removed')!r})", new_bb.get("removed") in ([], None) or len(new_bb.get("removed", [])) == 0)
    beat = new_bb.get("story_meta", {}).get("latest_beat", "")
    check(f"latest_beat 短小标签(实得 : {beat!r})", 0 < len(beat) <= 12 and "。" not in beat)
    check("未被 prompt 说明文字污染", no_schema_pollution(new_bb))


async def main() -> None:
    await scenario_1()
    await scenario_2()


if __name__ == "__main__":
    asyncio.run(main())
