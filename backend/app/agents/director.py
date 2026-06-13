import json

from pydantic import ValidationError

from app.agents.context import Blackboard, Message, build_messages
from app.config import settings
from app.llm.deepseek_client import get_client
from app.models.schemas import DirectorOutput


class DirectorOutputError(Exception):
    """Director 返回内容无法解析/校验时抛出,携带原始响应文本便于调试。"""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


async def run_director(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    knowledge: str = "",
    messages: list[Message] | None = None,
) -> DirectorOutput:
    client = get_client()
    # messages 由调用方预构造时直接复用(为了把「真正喂给 LLM 的完整 messages」原样存档,
    # M4.5-B)。不传则照常自行构造。build_messages 逻辑与缓存不受影响。
    if messages is None:
        messages = build_messages(
            "director",
            history=history,
            blackboard=blackboard,
            user_action=user_action,
            knowledge=knowledge,
        )

    response = await client.chat.completions.create(
        model=settings.deepseek_model,
        messages=messages,
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or ""

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DirectorOutputError(f"JSON 解析失败: {exc}", raw) from exc

    try:
        return DirectorOutput.model_validate(data)
    except ValidationError as exc:
        raise DirectorOutputError(f"Schema 校验失败: {exc}", raw) from exc
