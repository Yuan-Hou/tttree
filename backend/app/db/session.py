from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db import models  # noqa: F401  确保 ORM 模型注册到 Base.metadata
from app.db.base import Base

# DB 文件落在 backend/ 下(被 .gitignore 的 *.db 规则忽略)
DB_PATH = Path(__file__).resolve().parent.parent.parent / "vore.db"
DEFAULT_URL = f"sqlite+aiosqlite:///{DB_PATH}"


def make_engine(url: str = DEFAULT_URL) -> AsyncEngine:
    return create_async_engine(url, future=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(conn) -> None:
    """create_all 只建缺失的表、不给已存在的表补列。手动补我们新增的列(幂等)。
    全新库走 create_all 已带列、这里 PRAGMA 命中即跳过;旧库在此 ALTER 补上。"""
    from sqlalchemy import text

    cols = {r[1] for r in conn.execute(text("PRAGMA table_info(image_gens)")).fetchall()}
    if "superseded" not in cols:
        conn.execute(text("ALTER TABLE image_gens ADD COLUMN superseded BOOLEAN NOT NULL DEFAULT 0"))


# 应用级默认引擎/会话工厂(仅在被实际使用时才会创建 DB 文件)
engine = make_engine()
async_session = make_session_factory(engine)
