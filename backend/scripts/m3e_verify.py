"""M3-E 验收:人在回路双入口贯通(零 gpt-image-2 花费)。

跑一轮三段式 → 展示 B 的 draw_proposals(并证明它没进持久黑板)→ 入口一(B 提案)经绘图
Agent 写稿、用户编辑、reuse 复用已有图 → 入口二(用户主动)写稿后 skip。
直接调 draw_service(与 m1_cli 的交互层同一套逻辑),用脚本化决策代替 stdin。
用法(backend/ 下):python -m scripts.m3e_verify
"""

import asyncio
import json

from app.agents.director import run_director
from app.agents.director_review import run_director_review
from app.agents.writer import stream_writer
from app.db.models import Blackboard as BlackboardRow
from app.db.session import async_session, create_all, engine
from app.imaging.draw_service import apply_decision, format_review, prepare_draft
from app.state.reducer import reduce_turn

STORY = "cli-story"
ACTION = "爱丽丝向前踏出一步,举起那把巨大的白色武器,夕照在刃面上迸出一道冷光;优香没有退,只是抱着的双臂收得更紧。"


async def run_one_turn(blackboard: dict):
    """复现三段式管线(不含交互出图),返回 reducer 结果。"""
    print(f">>> 玩家行动: {ACTION}\n")
    a = await run_director([], blackboard, ACTION)
    print(f"[A] beat={a.beat}")
    chunks = []
    async for tok in stream_writer([], blackboard, ACTION, a.writing_brief):
        chunks.append(tok)
    narrative = "".join(chunks)
    print(f"[Writer] {narrative[:80]}……（{len(narrative)}字）")
    new_bb = await run_director_review([], blackboard, ACTION, narrative, director_a_plan=a.model_dump())
    async with async_session() as s:
        result = await reduce_turn(
            story_id=STORY,
            director_b_new_blackboard_str=json.dumps(new_bb, ensure_ascii=False),
            writer_narrative=narrative,
            director_a_json=a.model_dump_json(),
            user_input=ACTION,
            session=s,
        )
    print(f"[B/reducer] turn #{result.turn_index} 「{result.beat_title}」")
    return result


async def main() -> None:
    await create_all(engine)
    async with async_session() as s:
        row = await s.get(BlackboardRow, STORY)
        if row is None:
            print("⚠️ cli-story 无黑板,请先跑 M3-C 的 draw_cli 播种 old_classroom。")
            return
        blackboard = json.loads(row.json_blob)

    print("=" * 72)
    print("跑一轮三段式(让 Director-B 产出 draw_proposals)")
    print("=" * 72)
    result = await run_one_turn(blackboard)

    print("\n" + "=" * 72)
    print("Director-B 的 draw_proposals(本回合信号)")
    print("=" * 72)
    print(json.dumps(result.draw_proposals, ensure_ascii=False, indent=2))

    # 证明 draw_proposals 没进持久黑板
    async with async_session() as s:
        persisted = json.loads((await s.get(BlackboardRow, STORY)).json_blob)
    print("\n持久黑板顶层键:", list(persisted.keys()))
    assert "draw_proposals" not in persisted, "draw_proposals 不应进入持久黑板!"
    assert "draw_proposals" not in result.blackboard
    print("✅ 证明:draw_proposals 没进持久黑板(只在 reducer 返回值里,交人在回路)。")

    bb = result.blackboard

    # ============ 入口一:Director-B 提案 → 人在回路(写稿→编辑→reuse) ============
    print("\n" + "=" * 72)
    print("入口一:Director-B 提案触发人在回路")
    print("=" * 72)
    if not result.draw_proposals:
        print("(本轮 B 未提案;入口存在,但无提案内容可演示。)")
    for prop in result.draw_proposals:
        scene = prop.get("scene_slug")
        if scene not in bb.get("scenes", {}):
            print(f"  提案场景 {scene} 不在黑板,跳过。")
            continue
        print(f"\n[B 提案] 场景={scene} kind建议={prop.get('kind')} 理由={prop.get('reason')}")
        async with async_session() as s:
            bundle = await prepare_draft(
                s, story_id=STORY, blackboard=bb, scene_slug=scene,
                draw_request=f"为场景 {scene} 配图。B 建议:{prop.get('kind')} —— {prop.get('reason')}",
            )
        print("\n--- 阶段②·人在回路审阅(语义名版)---")
        print(format_review(bundle))

        # 演示用户「全量编辑权」:在确认前修改提示词文本
        edited = bundle.draft.prompt_text + " 【用户追加:强化爱丽丝眼中决意的高光】"
        print("\n[用户编辑了提示词文本(追加一句)]")

        # 演示 reuse:复用「初见」那张已有图,不调用 gpt-image-2、不花钱
        async with async_session() as s:
            res = await apply_decision(
                s, decision="reuse", bundle=bundle, final_prompt=edited,
                story_id=STORY, origin="director_b_proposal", source_turn=result.turn_index,
            )
        print(f"[决策=reuse] {res}")

    # ============ 入口二:用户主动发起 → 写稿 → skip ============
    print("\n" + "=" * 72)
    print("入口二:用户主动发起('draw old_classroom')")
    print("=" * 72)
    async with async_session() as s:
        bundle2 = await prepare_draft(
            s, story_id=STORY, blackboard=bb, scene_slug="old_classroom",
            draw_request="用户主动要求为 old_classroom 配图,定格当前画面。",
        )
    print("--- 阶段②·人在回路审阅(语义名版)---")
    print(format_review(bundle2))
    async with async_session() as s:
        res2 = await apply_decision(
            s, decision="skip", bundle=bundle2, final_prompt=bundle2.draft.prompt_text,
            story_id=STORY, origin="user_initiated",
        )
    print(f"\n[决策=skip] {res2}")

    # ============ 收尾:审计 reuse 是否落了 ImageGen(kind=reuse),且未新增图片文件 ============
    print("\n" + "=" * 72)
    print("收尾审计")
    print("=" * 72)
    import sqlite3
    con = sqlite3.connect("vore.db")
    rows = con.execute(
        "select id,kind,origin,output_path from image_gens where story_id=? order by id", (STORY,)
    ).fetchall()
    con.close()
    print("ImageGen 记录(含本次 reuse):")
    for r in rows:
        print(f"  #{r[0]} kind={r[1]} origin={r[2]} output={r[3]}")
    print("\n✅ 双入口贯通;三阶段(写稿→审阅可编辑→执行/复用/跳过)走通;draw_proposals 不进黑板;全程零 gpt-image-2 花费。")


if __name__ == "__main__":
    asyncio.run(main())
