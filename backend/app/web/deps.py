from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session


async def get_session() -> AsyncIterator[AsyncSession]:
    # 动态取会话工厂(而非 import 时绑定),便于测试用 monkeypatch 替换为临时库,
    # 不依赖「谁先 import app.main」的脆弱顺序。生产行为不变。
    async with db_session.async_session() as session:
        yield session
