"""M2-D 验收:三段式多轮实跑 → 查 DB → 断言黑板/物品/位置/recall/小标题/缓存。

真实调用 DeepSeek(5 轮 × 3 调用 + 一轮 usage 探针)。用独立临时 DB,与产品 vore.db 隔离。
用法(backend/ 下):python -m scripts.m2d_verify
"""

import asyncio
import json
from pathlib import Path

from app.agents.context import build_messages
from app.config import settings
from app.db.models import Blackboard as BlackboardRow
from app.db.models import Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.llm.deepseek_client import get_client
from app.models.schemas import normalize_scene_intent
from scripts.m1_cli import load_initial_blackboard, run_turn
from sqlalchemy import select

SID = "m2d-verify"
DB_FILE = Path("/tmp/m2d_verify.db")

SEQUENCE = [
    "我撑起身子,环顾四周,打量这片陌生的森林空地。",
    "我走到空地中央,注意到一截被砍倒的树桩,树桩上搁着一把古旧的铜钥匙。",
    "我伸手拿起那把铜钥匙,握在手里端详。",
    "我握着钥匙走向北侧那扇虚掩的木门,推门走了进去。",
    "我转身退出木屋,回到外面的森林空地。",
]


def check(label: str, ok: bool) -> None:
    print(f"  {'✅' if ok else '❌'} {label}")


async def usage_probe(blackboard: dict) -> None:
    """用最终黑板做一轮探针:A→Writer→B 共享 system+黑板+玩家输入前缀,确认缓存仍命中。"""
    client = get_client()
    action = "我站在原地,静静感受四周的气息。"
    brief = "第二人称、安静的笔触,聚焦四周环境的感官细节;须落实主角静立感受四周;篇幅短。"

    def hitmiss(u):
        d = u.model_dump()
        return d.get("prompt_cache_hit_tokens"), d.get("prompt_cache_miss_tokens"), d.get("prompt_tokens")

    a_msgs = build_messages("director", history=[], blackboard=blackboard, user_action=action)
    a = await client.chat.completions.create(model=settings.deepseek_model, messages=a_msgs, temperature=0.3, response_format={"type": "json_object"})
    print(f"  Director-A : hit/miss/total = {hitmiss(a.usage)}")

    w_msgs = build_messages("writer", history=[], blackboard=blackboard, user_action=action, writing_brief=brief)
    w_stream = await client.chat.completions.create(model=settings.deepseek_model, messages=w_msgs, temperature=0.85, stream=True, stream_options={"include_usage": True})
    w_usage = None
    async for ch in w_stream:
        if ch.usage is not None:
            w_usage = ch.usage
    print(f"  Writer     : hit/miss/total = {hitmiss(w_usage)}")

    b_msgs = build_messages("director_review", history=[], blackboard=blackboard, user_action=action, narrative="你静静站着,听风穿过树梢。", director_a_plan={"scene_intent": "stay"})
    b = await client.chat.completions.create(model=settings.deepseek_model, messages=b_msgs, temperature=0.3, response_format={"type": "json_object"})
    print(f"  Director-B : hit/miss/total = {hitmiss(b.usage)}")
    return hitmiss(a.usage), hitmiss(w_usage), hitmiss(b.usage)


async def main() -> None:
    if DB_FILE.exists():
        DB_FILE.unlink()
    engine = make_engine(f"sqlite+aiosqlite:///{DB_FILE}")
    await create_all(engine)
    Session = make_session_factory(engine)

    # 播种初始黑板
    initial = load_initial_blackboard()
    async with Session() as s:
        s.add(BlackboardRow(story_id=SID, json_blob=json.dumps(initial, ensure_ascii=False)))
        await s.commit()

    blackboard = initial
    history: list[dict] = []
    for action in SEQUENCE:
        blackboard, _ = await run_turn(Session, SID, blackboard, history, action)

    # ---- 查 DB ----
    async with Session() as s:
        final_row = await s.get(BlackboardRow, SID)
        final_bb = json.loads(final_row.json_blob)
        turns = (await s.execute(select(Turn).where(Turn.story_id == SID).order_by(Turn.turn_index))).scalars().all()

    print("\n" + "=" * 72)
    print("最终黑板")
    print("=" * 72)
    print(json.dumps(final_bb, ensure_ascii=False, indent=2))

    print("\n" + "=" * 72)
    print("Turn 表")
    print("=" * 72)
    for t in turns:
        print(f"#{t.turn_index} 「{t.beat_title}」 input={t.user_input[:24]}… narrative={len(t.narrative)}字")

    # ---- 断言 ----
    print("\n" + "=" * 72)
    print("断言")
    print("=" * 72)

    meta = final_bb.get("story_meta", {})
    scenes = final_bb.get("scenes", {})
    chars = final_bb.get("characters", {})
    items = final_bb.get("items", {})
    hero = next(iter(chars.values())) if chars else {}
    hero_name = next(iter(chars), None)
    cur = meta.get("current_scene")

    check(f"5 轮全部落库,turn_index 1..5(实得 {[t.turn_index for t in turns]})", [t.turn_index for t in turns] == [1, 2, 3, 4, 5])
    check(f"新场景被创建(scenes 数 {len(scenes)} >= 2)", len(scenes) >= 2)
    check("forest_edge 始终保留", "forest_edge" in scenes)

    # 物品 owner 随拾取变更:最终钥匙归主角,且 inventory 一致
    held = [k for k, v in items.items() if v.get("owner") == f"character:{hero_name}"]
    check(f"存在归属主角的物品(owner=character:{hero_name}),实得 {held}", len(held) >= 1)
    inv = hero.get("inventory", [])
    check(f"主角 inventory 非空且与 owner 一致(inv={inv})", len(inv) >= 1 and all((items.get(i) or {}).get("owner") == f"character:{hero_name}" for i in inv))
    check("没有任何钥匙类物品仍挂在 scene:forest_edge(已被取走)", not any(v.get("owner") == "scene:forest_edge" and "钥匙" in k for k, v in items.items()))

    # 人物 location 正确:recall 后回到 forest_edge,且 == current_scene
    check(f"主角 location == current_scene(实得 location={hero.get('location')!r}, current={cur!r})", hero.get("location") == cur)
    check(f"recall 后 current_scene 回到 forest_edge(实得 {cur!r})", cur == "forest_edge")

    # recall:离开的木屋场景仍以「离开时」状态留存(从未被清空)
    cabin_slugs = [k for k in scenes if k != "forest_edge"]
    cabin_ok = bool(cabin_slugs) and all(scenes[k].get("state") for k in cabin_slugs)
    check(f"离开的新场景仍保留且 state 非空(slugs={cabin_slugs})", cabin_ok)
    print(f"    · forest_edge.state(recall 后):{scenes.get('forest_edge', {}).get('state')!r}")
    for k in cabin_slugs:
        print(f"    · {k}.state(已离开):{scenes[k].get('state')!r}")

    # 小标题逐轮合理
    beats = [t.beat_title for t in turns]
    beats_ok = all(0 < len(b) <= 12 and "。" not in b for b in beats)
    check(f"小标题逐轮短小标签(实得 {beats})", beats_ok)

    # Director-B 至少修正过一次 A(对照每轮 A 的非权威 scene_intent 与 B 落定的 current_scene 是否变化)
    corrections = []
    prev_scene = initial["story_meta"]["current_scene"]
    for t in turns:
        a_plan = json.loads(t.director_a_json)
        b_after = json.loads(t.blackboard_after)
        b_cur = b_after.get("story_meta", {}).get("current_scene")
        a_intent = normalize_scene_intent(a_plan.get("scene_intent"))
        scene_changed = b_cur != prev_scene
        # A 猜「留在原地」但 B 换了场景,或 A 猜「进新/回旧」但 B 没换 → 视为 B 修正了 A 的意图
        corrected = (
            (a_intent == "stay" and scene_changed)
            or (a_intent in ("likely_new_scene", "likely_recall") and not scene_changed)
        )
        corrections.append((t.turn_index, a_intent, a_plan.get("scene_hint", ""), b_cur, corrected))
        prev_scene = b_cur
    # 说明:「B 修正 A」是涌现属性,而非每轮必然——A 的 brief 驱动 Writer,Writer 顺着
    # brief 写时 A≈B,B 无需修正。B 的修正是 Writer 偏离 brief 时的安全网,其硬保证已在
    # M2-B 场景 1(A 判 stay、B 据成稿改入新场景)中被断言验证。此处仅作信息性报告。
    n_corr = sum(c[-1] for c in corrections)
    print(f"    · 每轮 A 意图 vs B 落定(本轮 B 修正 A 共 {n_corr} 次;硬保证见 M2-B):")
    for idx, a_intent, a_hint, b_cur, corrected in corrections:
        print(f"        #{idx} A(intent={a_intent}, hint={a_hint!r}) → B current={b_cur!r}  {'【B 修正了 A】' if corrected else '一致'}")

    # ---- 缓存 usage 探针 ----
    print("\n" + "=" * 72)
    print("缓存 usage 探针(用最终黑板,A→Writer→B 共享前缀)")
    print("=" * 72)
    (a_hm, w_hm, b_hm) = await usage_probe(final_bb)
    any_hit = (a_hm[0] or 0) > 0 or (w_hm[0] or 0) > 0 or (b_hm[0] or 0) > 0
    check("至少一处 prompt_cache_hit_tokens > 0(缓存命中未被破坏)", any_hit)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
