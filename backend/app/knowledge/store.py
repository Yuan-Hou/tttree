"""设定圣经库的读写。本质是每故事一大篇自由文本的整存整取。

约定:agent 只读(get_knowledge),用户只写(set_knowledge / clear_knowledge)——
知识库不是动态状态,是用户精选的恒定设定底座,agent 永不修改。每故事独立。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Knowledge


async def get_knowledge(session: AsyncSession, story_id: str) -> str:
    """读取整篇。无该行(新故事/已清空)时返回空串。agent 与用户都走这里读。"""
    row = await session.get(Knowledge, story_id)
    return row.content if row else ""


async def set_knowledge(session: AsyncSession, story_id: str, content: str) -> Knowledge:
    """整篇覆盖写入(upsert)。一故事一行。"""
    row = await session.get(Knowledge, story_id)
    if row is None:
        row = Knowledge(story_id=story_id, content=content)
        session.add(row)
    else:
        row.content = content
    await session.commit()
    await session.refresh(row)
    return row


async def clear_knowledge(session: AsyncSession, story_id: str) -> bool:
    """清空某故事的知识库(删除该行)。无该行返回 False。"""
    row = await session.get(Knowledge, story_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True
