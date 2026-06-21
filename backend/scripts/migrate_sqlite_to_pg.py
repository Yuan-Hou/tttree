"""一次性 ETL:把现有 SQLite 库(backend/vore.db)整库搬进 Postgres。

背景:服务从 SQLite 迁到 Postgres(为 Docker 化 + 论坛并发)。schema 由 Alembic 掌管;本脚本
负责把**数据**从旧 SQLite 文件复制到目标 PG,并重置自增序列。源 SQLite 已是当前 schema(你一直
在用的库,早被历史的在地补列逻辑补齐),故逐表原样复制即可、无需结构转换。

前置:目标 PG 用 DATABASE_URL 指定(postgres://… 或 postgresql+asyncpg://…)。脚本会先对目标跑
`alembic upgrade head` 建好空 schema,再灌数据。

用法(backend/ 下):
    DATABASE_URL=postgresql+asyncpg://user:pw@host:5432/voretree python -m scripts.migrate_sqlite_to_pg
可选 --source 指定源 SQLite 文件(默认 backend/vore.db);--yes 跳过确认。

幂等性:不是幂等的。目标表非空时默认中止(避免重复灌入),除非加 --force。
"""

import argparse
import asyncio
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
from app.db import models  # noqa: F401  注册全表到 metadata
from app.db.session import DB_PATH, resolve_url

# 自增整型主键的表 —— 灌完数据后要把 PG 序列推到 max(id)+1,否则后续 INSERT 撞主键。
_SERIAL_TABLES = ("turns", "image_gens", "reference_assets", "draw_proposals")


def _ensure_target_schema() -> None:
    """对目标(DATABASE_URL)跑 alembic upgrade head,建好空 schema 并 stamp 版本。"""
    cfg = Config("alembic.ini")  # 须在 backend/ 下运行;env.py 从 DATABASE_URL 取连接
    command.upgrade(cfg, "head")


async def _copy(source_url: str, target_url: str, force: bool) -> None:
    src = create_async_engine(source_url)
    dst = create_async_engine(target_url)
    try:
        tables = Base.metadata.sorted_tables  # 无外键约束,顺序无所谓
        async with src.connect() as sconn, dst.begin() as dconn:
            # 先确认目标干净(任一表有数据就中止,除非 --force)
            for t in tables:
                n = (await dconn.execute(select(func.count()).select_from(t))).scalar_one()
                if n and not force:
                    raise SystemExit(
                        f"目标表 {t.name} 已有 {n} 行 —— 疑似重复迁移。确认要继续请加 --force。"
                    )

            total = 0
            for t in tables:
                rows = [dict(r) for r in (await sconn.execute(select(t))).mappings().all()]
                if rows:
                    await dconn.execute(t.insert(), rows)
                print(f"  {t.name:18s} {len(rows):>6d} 行")
                total += len(rows)

            # 重置 PG 自增序列到当前最大 id
            if dst.dialect.name == "postgresql":
                from sqlalchemy import text

                for name in _SERIAL_TABLES:
                    await dconn.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                            f"COALESCE((SELECT MAX(id) FROM {name}), 1), "
                            f"(SELECT COUNT(*) FROM {name}) > 0)"
                        )
                    )
            print(f"完成:共复制 {total} 行,序列已重置。")
    finally:
        await src.dispose()
        await dst.dispose()


def main() -> None:
    ap = argparse.ArgumentParser(description="SQLite → Postgres 一次性数据迁移")
    ap.add_argument("--source", default=None, help="源 SQLite 文件(默认 backend/vore.db)")
    ap.add_argument("--force", action="store_true", help="目标表非空也继续(危险)")
    ap.add_argument("--yes", action="store_true", help="跳过确认提示")
    args = ap.parse_args()

    source_path = args.source or str(DB_PATH)
    source_url = f"sqlite+aiosqlite:///{source_path}"
    target_url = resolve_url()

    if target_url.startswith("sqlite"):
        sys.exit("DATABASE_URL 未指向 Postgres(当前回退到 SQLite)。请先设置 DATABASE_URL 再运行。")

    print(f"源:  {source_url}")
    print(f"目标:{target_url}")
    if not args.yes:
        if input("确认开始迁移?[y/N] ").strip().lower() != "y":
            sys.exit("已取消。")

    print("→ 对目标跑 alembic upgrade head …")
    _ensure_target_schema()
    print("→ 复制数据 …")
    asyncio.run(_copy(source_url, target_url, args.force))


if __name__ == "__main__":
    main()
