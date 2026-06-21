from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.db import models  # noqa: F401  确保 ORM 模型注册到 Base.metadata
from app.db.base import Base

# DB 文件落在 backend/ 下(被 .gitignore 的 *.db 规则忽略)。仅当未配置 DATABASE_URL 时用作回退。
DB_PATH = Path(__file__).resolve().parent.parent.parent / "vore.db"
SQLITE_FALLBACK_URL = f"sqlite+aiosqlite:///{DB_PATH}"


def _normalize_url(url: str) -> str:
    """把常见 Postgres URL 归一到 asyncpg 驱动(运维通常只写 postgres:// 或 postgresql://)。
    sqlite 等其余串原样返回。"""
    for prefix in ("postgresql+asyncpg://", "sqlite"):
        if url.startswith(prefix):
            return url
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def resolve_url() -> str:
    """DATABASE_URL(环境变量 / .env)→ 归一;留空则回退到本地 SQLite 文件。"""
    return _normalize_url(settings.database_url.strip()) if settings.database_url.strip() else SQLITE_FALLBACK_URL


def make_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or resolve_url(), future=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_all(engine: AsyncEngine) -> None:
    """从 ORM 元数据建全部缺失的表(幂等)。供测试/本地 SQLite 起库用;**生产 schema 由 Alembic 掌管**
    (entrypoint 跑 `alembic upgrade head`)。历史上的 SQLite 在地补列逻辑(_add_missing_columns)已退役
    —— 它是 Alembic 之前的过渡产物,且 PRAGMA 仅 SQLite 可用;现有 vore.db 的演进改由一次性 ETL +
    Alembic 接管(见 scripts/migrate_sqlite_to_pg.py 与 alembic/versions/ 基线)。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# 应用级默认引擎/会话工厂(仅在被实际使用时才会创建 DB 文件)
engine = make_engine()
async_session = make_session_factory(engine)
