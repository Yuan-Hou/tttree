import json
from collections.abc import AsyncIterator

from pydantic import ValidationError

from app.agents.context import Blackboard, Message, build_messages
from app.llm.chat import chat_json, chat_json_stream
from app.llm.jsonout import loads_lenient
from app.models.schemas import DirectorOutput


class DirectorOutputError(Exception):
    """Director 返回内容无法解析/校验时抛出,携带原始响应文本便于调试。"""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def parse_director_output(raw: str) -> DirectorOutput:
    """把 LLM 原始文本解析+校验为 DirectorOutput(失败抛 DirectorOutputError,携带 raw)。
    一次性路径(run_director)与流式路径(stream_director 累积后)共用,保证容错语义一致。"""
    try:
        data = loads_lenient(raw)
    except json.JSONDecodeError as exc:
        raise DirectorOutputError(f"JSON 解析失败: {exc}", raw) from exc
    try:
        return DirectorOutput.model_validate(data)
    except ValidationError as exc:
        raise DirectorOutputError(f"Schema 校验失败: {exc}", raw) from exc


def build_director_messages(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    knowledge: str = "",
) -> list[Message]:
    return build_messages(
        "director", history=history, blackboard=blackboard, user_action=user_action, knowledge=knowledge
    )


async def stream_director(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    knowledge: str = "",
    messages: list[Message] | None = None,
    model: str | None = None,
) -> AsyncIterator[str]:
    """逐 token 流式产 Director-A 的原始 JSON 文本(调用方累积后用 parse_director_output 解析)。
    messages/model 语义与 run_director 一致;前缀缓存不受 stream 影响(messages 逐字节不变)。"""
    if messages is None:
        messages = build_director_messages(history, blackboard, user_action, knowledge)
    async for delta in chat_json_stream(model, messages):
        yield delta


async def run_director(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    knowledge: str = "",
    messages: list[Message] | None = None,
    model: str | None = None,
) -> DirectorOutput:
    # messages 由调用方预构造时直接复用(为了把「真正喂给 LLM 的完整 messages」原样存档,
    # M4.5-B)。不传则照常自行构造。build_messages 逻辑与缓存不受影响。
    # model 由 orchestration 按故事内设置解析后传入;None → registry 回落默认(deepseek)。
    if messages is None:
        messages = build_director_messages(history, blackboard, user_action, knowledge)

    raw = await chat_json(model, messages)
    return parse_director_output(raw)
