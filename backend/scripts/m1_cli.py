"""M1 验证脚本: Director -> Writer 串行链路(共享上下文 + 缓存友好布局)。

用法(在 backend/ 目录下):
    python -m scripts.m1_cli                 # 交互模式
    python -m scripts.m1_cli "推开阁楼的门"   # 单次模式
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.agents.context import Message
from app.agents.director import DirectorOutputError, run_director
from app.agents.writer import stream_writer
from app.models.schemas import DirectorOutput, WorldState

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def load_initial_state() -> WorldState:
    data = json.loads((FIXTURES_DIR / "initial_state.json").read_text(encoding="utf-8"))
    return WorldState.model_validate(data)


def apply_director_output(state: WorldState, output: DirectorOutput) -> None:
    """占位的朴素状态合并逻辑,M2 会替换为独立的 reducer agent。

    注意:本函数只能在 Director 与 Writer **都**调用完之后执行——两个 agent 必须收到
    逐字节相同的世界状态快照,才能命中同一回合内的前缀缓存。"""
    state.scenes.setdefault(output.scene_id, {}).update(output.scene_delta)
    state.current_scene_id = output.scene_id

    for char_id, delta in output.character_updates.items():
        state.characters.setdefault(char_id, {}).update(delta)

    state.story_summary = output.story_summary_update


def check_must_include(narrative: str, must_include: list[str]) -> None:
    print("\n--- must_include 核对(逐字匹配,未命中需人工检查是否被意译落实) ---")
    if not must_include:
        print("(本回合 writing_brief.must_include 为空)")
    for item in must_include:
        mark = "✅" if item in narrative else "⚠️ "
        print(f"{mark} {item}")
    print("-------------------------------------------------------------")


async def run_turn(state: WorldState, history: list[Message], user_action: str) -> None:
    print(f"\n>>> 玩家行动: {user_action}\n")

    # Director 与 Writer 共享同一个(本回合尚未变更的)world_state 快照。
    try:
        director_output = await run_director(history, state, user_action)
    except DirectorOutputError as exc:
        print(f"[Director 输出异常] {exc}")
        print(f"原始返回:\n{exc.raw}")
        return

    print("--- Director 输出(已解析) ---")
    print(director_output.model_dump_json(indent=2))
    print("--------------------------------\n")

    print("--- Writer 输出(流式) ---")
    chunks: list[str] = []
    async for token in stream_writer(
        history, state, user_action, director_output.writing_brief
    ):
        print(token, end="", flush=True)
        chunks.append(token)
    narrative = "".join(chunks)
    print("\n--------------------------")

    check_must_include(narrative, director_output.writing_brief.must_include)

    # 两个 agent 都跑完后:先把「干净」消息追加进历史,再合并世界状态。
    history.append({"role": "user", "content": user_action})
    history.append({"role": "assistant", "content": narrative})
    apply_director_output(state, director_output)

    if director_output.choices:
        print("\n建议的下一步行动:")
        for choice in director_output.choices:
            print(f"  - {choice}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="M1 Director/Writer 验证脚本")
    parser.add_argument("action", nargs="?", help="玩家行动(留空则进入交互模式)")
    args = parser.parse_args()

    state = load_initial_state()
    history: list[Message] = []

    if args.action:
        await run_turn(state, history, args.action)
        return

    print("交互模式,输入 'quit' 或按 Ctrl-D 退出。\n")
    print(f"开场: {state.story_summary}\n")
    while True:
        try:
            user_action = input("你的行动> ").strip()
        except EOFError:
            break
        if not user_action:
            continue
        if user_action.lower() in {"quit", "exit"}:
            break
        await run_turn(state, history, user_action)


if __name__ == "__main__":
    asyncio.run(main())
