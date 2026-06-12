import json

from pydantic import ValidationError

from app.agents.context import Message, build_messages
from app.config import settings
from app.llm.deepseek_client import get_client
from app.models.schemas import DirectorOutput, WorldState


class DirectorOutputError(Exception):
    """Director 返回内容无法解析/校验时抛出,携带原始响应文本便于调试。"""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


async def run_director(
    history: list[Message],
    world_state: WorldState,
    user_action: str,
) -> DirectorOutput:
    client = get_client()
    messages = build_messages(
        "director",
        history=history,
        world_state=world_state,
        user_action=user_action,
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
