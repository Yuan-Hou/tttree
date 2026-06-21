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

    # 用户系统:故事归属。旧库现有故事一律回填 owner_id="1"(1 号用户拥有此前全部数据)。
    story_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(stories)")).fetchall()}
    if story_cols and "owner_id" not in story_cols:
        conn.execute(text("ALTER TABLE stories ADD COLUMN owner_id VARCHAR NOT NULL DEFAULT '1'"))

    # 全局设置从「全站单例」改为「按用户」:旧的 'singleton' 行归 1 号用户(仅当 '1' 行尚不存在)。
    appset_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(app_settings)")).fetchall()}
    if appset_cols:
        has_one = conn.execute(text("SELECT 1 FROM app_settings WHERE id='1'")).fetchone()
        if not has_one:
            conn.execute(text("UPDATE app_settings SET id='1' WHERE id='singleton'"))

    # Options agent(里程碑:tips + 选项):turns 存它的输出与上下文、story_settings 存它的模型覆盖。
    turn_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(turns)")).fetchall()}
    if turn_cols:  # 表已存在(旧库)才补;全新库由 create_all 直接带列
        for col in ("options_json", "options_messages"):
            if col not in turn_cols:
                conn.execute(text(f"ALTER TABLE turns ADD COLUMN {col} TEXT DEFAULT ''"))
    ss_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(story_settings)")).fetchall()}
    if ss_cols and "options_model" not in ss_cols:
        conn.execute(text("ALTER TABLE story_settings ADD COLUMN options_model VARCHAR DEFAULT ''"))
    # 故事内自定义圣经(bible 子步):空串 = 用全局打包默认。
    if ss_cols:
        for col in ("style_bible", "visual_style_bible"):
            if col not in ss_cols:
                conn.execute(text(f"ALTER TABLE story_settings ADD COLUMN {col} TEXT DEFAULT ''"))
    # 绘图模型覆盖(image_model 子步):空串 = 用全局默认绘图模型。
    if ss_cols and "image_model" not in ss_cols:
        conn.execute(text("ALTER TABLE story_settings ADD COLUMN image_model VARCHAR DEFAULT ''"))


# 应用级默认引擎/会话工厂(仅在被实际使用时才会创建 DB 文件)
engine = make_engine()
async_session = make_session_factory(engine)
