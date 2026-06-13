"""M2 三段式 CLI: 每轮 Director-A → Writer → Director-B → reducer。

黑板是唯一世界真相(存于 DB)。回合内顺序铁律:A、Writer、B 都看同一份「本回合之前」的
黑板;reducer 在三次 LLM 调用全部完成后才写库。

用法(backend/ 下):
    python -m scripts.m1_cli                  # 交互模式
    python -m scripts.m1_cli "推开那扇木门"     # 单次模式
"""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.context import Blackboard, Message
from app.agents.director import DirectorOutputError, run_director
from app.agents.director_review import DirectorReviewError, run_director_review
from app.agents.writer import stream_writer
from app.db.models import Blackboard as BlackboardRow
from app.db.session import async_session, create_all, engine
from app.imaging.draw_service import interactive_draw_session
from app.state.reducer import ReducerResult, reduce_turn

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
STORY_ID = "cli-story"


def load_initial_blackboard() -> Blackboard:
    return json.loads((FIXTURES_DIR / "initial_blackboard.json").read_text(encoding="utf-8"))


async def seed_if_needed(Session: async_sessionmaker, story_id: str) -> Blackboard:
    """建表;若该 story 尚无黑板则用初始黑板播种。返回当前黑板。"""
    await create_all(engine)
    async with Session() as s:
        row = await s.get(BlackboardRow, story_id)
        if row is not None:
            return json.loads(row.json_blob)
        bb = load_initial_blackboard()
        s.add(BlackboardRow(story_id=story_id, json_blob=json.dumps(bb, ensure_ascii=False)))
        await s.commit()
        return bb


async def run_turn(
    Session: async_sessionmaker,
    story_id: str,
    blackboard: Blackboard,
    history: list[Message],
    user_action: str,
) -> tuple[Blackboard, ReducerResult | None]:
    print(f"\n>>> 玩家行动: {user_action}\n")

    # ---- Director-A:读黑板,出预案(引导 Writer + 给 B 参考,不落盘)----
    try:
        a = await run_director(history, blackboard, user_action)
    except DirectorOutputError as exc:
        print(f"[Director-A 异常] {exc}\n原始返回:\n{exc.raw}")
        return blackboard, None
    print("--- Director-A 预案 ---")
    print(f"  beat: {a.beat}")
    print(f"  scene_event={a.scene_event}  scene_id={a.scene_id}  mood={a.mood}")
    print(f"  brief.must_include={a.writing_brief.must_include}")

    # ---- Writer:读同一份黑板 + brief,自由创作(流式)----
    print("\n--- Writer 叙事(流式) ---")
    chunks: list[str] = []
    async for token in stream_writer(history, blackboard, user_action, a.writing_brief):
        print(token, end="", flush=True)
        chunks.append(token)
    narrative = "".join(chunks)
    print("\n")

    # ---- Director-B:读同一份黑板 + 成稿 + A 预案,全量重写新黑板 ----
    try:
        new_bb = await run_director_review(
            history, blackboard, user_action, narrative, director_a_plan=a.model_dump()
        )
    except DirectorReviewError as exc:
        print(f"[Director-B 异常] {exc}\n原始返回:\n{exc.raw}")
        return blackboard, None

    # ---- reducer:三次 LLM 调用都完成后,才解析校验 + 写库 ----
    async with Session() as s:
        result = await reduce_turn(
            story_id=story_id,
            director_b_new_blackboard_str=json.dumps(new_bb, ensure_ascii=False),
            writer_narrative=narrative,
            director_a_json=a.model_dump_json(),
            user_input=user_action,
            session=s,
        )

    tag = f"  告警 {len(result.warnings)} 条" if result.warnings else ""
    print(f"--- reducer: turn #{result.turn_index} 「{result.beat_title}」{tag} ---")

    # 历史只追加「干净」消息(玩家输入 + 叙事),黑板不进历史
    history.append({"role": "user", "content": user_action})
    history.append({"role": "assistant", "content": narrative})

    if a.choices:
        print("建议下一步:" + "  |  ".join(a.choices))

    # ---- 入口一:Director-B 提案 → 人在回路出图(逐张确认;提案不进黑板,reducer 已剥离)----
    for prop in result.draw_proposals:
        scene = prop.get("scene_slug", "")
        print(f"\n[Director-B 出图提案] 场景={scene} kind建议={prop.get('kind')}  理由:{prop.get('reason')}")
        req = f"为场景 {scene} 配图。Director-B 的建议:{prop.get('kind')} —— {prop.get('reason')}"
        await interactive_draw_session(
            Session, story_id=story_id, blackboard=result.blackboard, scene_slug=scene,
            draw_request=req, origin="director_b_proposal", source_turn=result.turn_index,
        )

    return result.blackboard, result


async def main() -> None:
    parser = argparse.ArgumentParser(description="M2 三段式 CLI")
    parser.add_argument("action", nargs="?", help="玩家行动(留空进入交互模式)")
    args = parser.parse_args()

    Session = async_session
    blackboard = await seed_if_needed(Session, STORY_ID)
    history: list[Message] = []

    if args.action:
        await run_turn(Session, STORY_ID, blackboard, history, args.action)
        return

    print("交互模式,输入 'quit' 退出;输入 'draw <场景slug>' 主动发起出图。\n")
    print(f"开场:{blackboard['story_meta']['title']} —— {blackboard['scenes'][blackboard['story_meta']['current_scene']]['state']}\n")
    while True:
        try:
            user_action = input("你的行动> ").strip()
        except EOFError:
            break
        if not user_action:
            continue
        if user_action.lower() in {"quit", "exit"}:
            break
        # ---- 入口二:用户主动发起出图,无需 B 提议(同一确认门,逐张确认)----
        if user_action.lower().startswith("draw "):
            scene = user_action[5:].strip()
            req = f"用户主动要求为场景 {scene} 配图,定格其当前画面。"
            await interactive_draw_session(
                Session, story_id=STORY_ID, blackboard=blackboard, scene_slug=scene,
                draw_request=req, origin="user_initiated",
            )
            continue
        blackboard, _ = await run_turn(Session, STORY_ID, blackboard, history, user_action)


if __name__ == "__main__":
    asyncio.run(main())
