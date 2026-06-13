"""参考图库 CLI(增/查/改/删)。素材落 backend/storage/references/。

用法(backend/ 下):
    python -m scripts.refs_cli add  --file 路径 --label 主角立绘 --description "..." --category 角色
    python -m scripts.refs_cli list
    python -m scripts.refs_cli edit --id 1 --description "新的说明"
    python -m scripts.refs_cli delete --id 1
"""

import argparse
import asyncio
from pathlib import Path

from app.assets.reference_store import (
    VALID_CATEGORIES,
    add_reference,
    delete_reference,
    list_references,
    update_reference_description,
)
from app.db.session import async_session, create_all, engine
from app.storage import ensure_dirs

STORY_ID = "cli-story"


async def cmd_add(args: argparse.Namespace) -> None:
    async with async_session() as s:
        asset = await add_reference(
            s,
            story_id=args.story,
            label=args.label,
            description=args.description,
            category=args.category,
            source_file=Path(args.file),
        )
    print(f"✅ 已登记 #{asset.id} 「{asset.label}」[{asset.category}] -> {asset.file_path}")


async def cmd_list(args: argparse.Namespace) -> None:
    async with async_session() as s:
        assets = await list_references(s, args.story)
    if not assets:
        print("(参考图库为空)")
        return
    print(f"参考图库({len(assets)} 张):")
    for a in assets:
        print(f"  #{a.id} 「{a.label}」[{a.category}]  {a.file_path}")
        print(f"        说明: {a.description}")


async def cmd_edit(args: argparse.Namespace) -> None:
    async with async_session() as s:
        asset = await update_reference_description(s, args.id, args.description)
    print(f"✅ #{asset.id} 「{asset.label}」说明已更新为: {asset.description}")


async def cmd_delete(args: argparse.Namespace) -> None:
    async with async_session() as s:
        ok = await delete_reference(s, args.id)
    print(f"✅ 已删除 #{args.id}" if ok else f"⚠️  未找到 #{args.id}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="参考图库 CLI")
    parser.add_argument("--story", default=STORY_ID, help="story_id(默认 cli-story)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="登记一张参考图")
    p_add.add_argument("--file", required=True)
    p_add.add_argument("--label", required=True)
    p_add.add_argument("--description", default="")
    p_add.add_argument("--category", required=True, choices=VALID_CATEGORIES)
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="列出参考图")
    p_list.set_defaults(func=cmd_list)

    p_edit = sub.add_parser("edit", help="修改说明")
    p_edit.add_argument("--id", type=int, required=True)
    p_edit.add_argument("--description", required=True)
    p_edit.set_defaults(func=cmd_edit)

    p_del = sub.add_parser("delete", help="删除参考图")
    p_del.add_argument("--id", type=int, required=True)
    p_del.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    await create_all(engine)
    ensure_dirs()
    await args.func(args)


if __name__ == "__main__":
    asyncio.run(main())
