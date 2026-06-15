from collections.abc import AsyncIterator

from app.agents.context import Blackboard, Message, build_messages
from app.llm.registry import resolve_chat


async def stream_writer(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    writing_brief: str,
    messages: list[Message] | None = None,
    model: str | None = None,
) -> AsyncIterator[str]:
    # model 由 orchestration 按故事内设置解析后传入;None → registry 回落默认(deepseek)。
    client, model_name = resolve_chat(model)
    # 调用方预构造时直接复用(供 M4.5-B 原样存档真正喂给 LLM 的 messages);不传则照常构造。
    if messages is None:
        messages = build_messages(
            "writer",
            history=history,
            blackboard=blackboard,
            user_action=user_action,
            writing_brief=writing_brief,
        )

    stream = await client.chat.completions.create(
        model=model_name,
        messages=messages,
        stream=True,
    )

    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
