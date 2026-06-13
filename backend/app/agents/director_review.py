import json
from typing import Any

from app.agents.context import Blackboard, Message, build_messages
from app.config import settings
from app.llm.deepseek_client import get_client


class DirectorReviewError(Exception):
    """Director-B 返回内容无法解析为 JSON 时抛出,携带原始响应文本便于调试。"""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


async def run_director_review(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    narrative: str,
    director_a_plan: dict[str, Any] | None = None,
    messages: list[Message] | None = None,
) -> Blackboard:
    """读当前黑板 + Writer 本轮成稿(+ A 预案),全量重写出新黑板。

    返回解析后的新黑板 dict(权威,后续由 reducer 校验落盘)。本函数只负责取到可解析
    的 JSON,不做语义校验——那是 reducer 的职责。
    """
    client = get_client()
    # 调用方预构造时直接复用(供 M4.5-B 原样存档真正喂给 LLM 的 messages);不传则照常构造。
    if messages is None:
        messages = build_messages(
            "director_review",
            history=history,
            blackboard=blackboard,
            user_action=user_action,
            narrative=narrative,
            director_a_plan=director_a_plan,
        )

    response = await client.chat.completions.create(
        model=settings.deepseek_model,
        messages=messages,
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or ""

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DirectorReviewError(f"JSON 解析失败: {exc}", raw) from exc
