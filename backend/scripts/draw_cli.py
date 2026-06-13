"""绘图人在回路 CLI(用户主动入口)。三阶段:绘图Agent写稿 → 用户审阅/编辑 → 执行。

走与 m1_cli 完全相同的确认门:任何真正调用 gpt-image-2(花钱)前,都必须经过一次带编辑
能力的用户确认(键入 y)。**没有 --yes 之类的旁路**;非交互场景请把确认键通过 stdin 传入,
例如:  printf 'y\\n' | python -m scripts.draw_cli --scene old_classroom

用法(backend/ 下):
    python -m scripts.draw_cli --scene old_classroom
    printf 'e\\n<改后的提示词>\\ny\\n' | python -m scripts.draw_cli --scene old_classroom   # 先编辑再确认
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.db.models import Blackboard
from app.db.session import async_session, create_all, engine
from app.imaging.draw_service import interactive_draw_session
from app.storage import ensure_dirs

STORY = "cli-story"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


async def ensure_demo_blackboard(session) -> dict:
    row = await session.get(Blackboard, STORY)
    if row is None:
        bb = json.loads((FIXTURES / "visual_demo_blackboard.json").read_text(encoding="utf-8"))
        session.add(Blackboard(story_id=STORY, json_blob=json.dumps(bb, ensure_ascii=False)))
        await session.commit()
        return bb
    return json.loads(row.json_blob)


async def main() -> None:
    parser = argparse.ArgumentParser(description="绘图人在回路 CLI(用户主动入口)")
    parser.add_argument("--scene", required=True, help="目标场景 slug")
    parser.add_argument("--request", default=None, help="绘图请求(默认据场景自动生成)")
    parser.add_argument("--origin", default="user_initiated", choices=["user_initiated", "director_b_proposal"])
    parser.add_argument("--source-turn", type=int, default=None)
    args = parser.parse_args()

    await create_all(engine)
    ensure_dirs()

    async with async_session() as s:
        blackboard = await ensure_demo_blackboard(s)
    request = args.request or f"为当前场景 {args.scene} 画一张图,定格本回合的画面。"

    # 唯一确认门:写稿 → 审阅(可编辑)→ y/e/r/s。confirm 必经用户键入 y。
    await interactive_draw_session(
        async_session,
        story_id=STORY,
        blackboard=blackboard,
        scene_slug=args.scene,
        draw_request=request,
        origin=args.origin,
        source_turn=args.source_turn,
    )


if __name__ == "__main__":
    asyncio.run(main())
