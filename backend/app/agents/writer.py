from collections.abc import AsyncIterator

from app.agents.context import Blackboard, Message, build_messages
from app.config import settings
from app.llm.deepseek_client import get_client
from app.models.schemas import WritingBrief


async def stream_writer(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    writing_brief: WritingBrief,
) -> AsyncIterator[str]:
    client = get_client()
    messages = build_messages(
        "writer",
        history=history,
        blackboard=blackboard,
        user_action=user_action,
        writing_brief=writing_brief,
    )

    stream = await client.chat.completions.create(
        model=settings.deepseek_model,
        messages=messages,
        temperature=0.85,
        stream=True,
    )

    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
