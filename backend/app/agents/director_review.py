import json
from collections.abc import AsyncIterator
from typing import Any

from app.agents.context import Blackboard, Message, build_messages
from app.llm.chat import chat_json, chat_json_stream
from app.llm.jsonout import loads_lenient


class DirectorReviewError(Exception):
    """Director-B 返回内容无法解析为 JSON 时抛出,携带原始响应文本便于调试。"""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def parse_review_output(raw: str) -> Blackboard:
    """把 LLM 原始文本解析为新黑板 dict(只取可解析 JSON,语义校验留给 reducer)。
    一次性路径与流式路径共用,容错语义一致。"""
    try:
        return loads_lenient(raw)
    except json.JSONDecodeError as exc:
        raise DirectorReviewError(f"JSON 解析失败: {exc}", raw) from exc


def build_review_messages(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    narrative: str,
    director_a_plan: dict[str, Any] | None = None,
    tips: list[str] | None = None,
) -> list[Message]:
    return build_messages(
        "director_review", history=history, blackboard=blackboard, user_action=user_action,
        narrative=narrative, director_a_plan=director_a_plan, tips=tips,
    )


async def stream_director_review(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    narrative: str,
    director_a_plan: dict[str, Any] | None = None,
    tips: list[str] | None = None,
    messages: list[Message] | None = None,
    model: str | None = None,
) -> AsyncIterator[str]:
    """逐 token 流式产 Director-B 的原始 JSON 文本(调用方累积后用 parse_review_output 解析)。"""
    if messages is None:
        messages = build_review_messages(history, blackboard, user_action, narrative, director_a_plan, tips)
    async for delta in chat_json_stream(model, messages):
        yield delta


async def run_director_review(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    narrative: str,
    director_a_plan: dict[str, Any] | None = None,
    messages: list[Message] | None = None,
    model: str | None = None,
) -> Blackboard:
    """读当前黑板 + Writer 本轮成稿(+ A 预案),全量重写出新黑板。

    返回解析后的新黑板 dict(权威,后续由 reducer 校验落盘)。本函数只负责取到可解析
    的 JSON,不做语义校验——那是 reducer 的职责。
    """
    # 调用方预构造时直接复用(供 M4.5-B 原样存档真正喂给 LLM 的 messages);不传则照常构造。
    # model 由 orchestration 按故事内设置解析后传入;None → registry 回落默认(deepseek)。
    if messages is None:
        messages = build_review_messages(history, blackboard, user_action, narrative, director_a_plan)

    raw = await chat_json(model, messages)
    return parse_review_output(raw)
