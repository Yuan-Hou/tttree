from collections.abc import AsyncIterator

from app.agents.context import Blackboard, Message, build_messages
from app.llm.chat import chat_stream


async def stream_writer(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    writing_brief: str,
    messages: list[Message] | None = None,
    model: str | None = None,
) -> AsyncIterator[str]:
    # 调用方预构造时直接复用(供 M4.5-B 原样存档真正喂给 LLM 的 messages);不传则照常构造。
    # model 由 orchestration 按故事内设置解析后传入;None → registry 回落默认(deepseek)。
    if messages is None:
        messages = build_messages(
            "writer",
            history=history,
            blackboard=blackboard,
            user_action=user_action,
            writing_brief=writing_brief,
        )

    async for delta in chat_stream(model, messages):
        yield delta
