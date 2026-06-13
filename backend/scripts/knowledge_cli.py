"""设定圣经库读写 CLI(纯后端验证管线;Web 编辑框留给 M5)。

整篇覆盖写入 / 读取 / 清空某故事的知识库。agent 只读、用户只写,这里就是「用户」入口。

用法(backend/ 下):
    # 写入(从文件,整篇覆盖)
    python -m scripts.knowledge_cli set   --story cli-story --file path/to/setting.md
    # 写入(从 stdin)
    cat setting.md | python -m scripts.knowledge_cli set --story cli-story
    # 读取
    python -m scripts.knowledge_cli get   --story cli-story
    # 清空
    python -m scripts.knowledge_cli clear --story cli-story
"""

import argparse
import asyncio
import sys
from pathlib import Path

from app.db.session import async_session, create_all, engine
from app.knowledge.store import clear_knowledge, get_knowledge, set_knowledge


async def main() -> None:
    parser = argparse.ArgumentParser(description="设定圣经库读写 CLI")
    parser.add_argument("action", choices=["set", "get", "clear"])
    parser.add_argument("--story", required=True, help="story_id")
    parser.add_argument("--file", default=None, help="set:从该文件读入;省略则读 stdin")
    args = parser.parse_args()

    await create_all(engine)

    async with async_session() as s:
        if args.action == "set":
            content = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
            row = await set_knowledge(s, args.story, content)
            print(f"[set] story={args.story} 写入 {len(row.content)} 字,updated_at={row.updated_at}")
        elif args.action == "get":
            content = await get_knowledge(s, args.story)
            print(f"[get] story={args.story} 共 {len(content)} 字:\n{'-' * 40}\n{content}")
        else:  # clear
            ok = await clear_knowledge(s, args.story)
            print(f"[clear] story={args.story} {'已清空' if ok else '本就没有知识库'}")


if __name__ == "__main__":
    asyncio.run(main())
