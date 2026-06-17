"""Options agent:Writer 成稿后,出 1–3 个「下一步可选项」给玩家挑。

编排上是 Director-B 的并行兄弟(同读本轮之前的黑板 + 历史,易变区尾部各附 Writer 成稿与 tips),
但**互不依赖、互不影响**:Options 是叶子(无下游),reducer 只等 B,Options 失败不阻断落盘。
复用 build_messages 统一入口,前缀(system 文风圣经 + history)与 A/Writer/B 逐字节一致,缓存命中。
"""

import json

from app.agents.context import Blackboard, Message, build_messages
from app.llm.registry import resolve_chat
from app.models.schemas import OptionsOutput


class OptionsError(Exception):
    """Options 返回内容无法解析/校验时抛出,携带原始响应文本便于调试。"""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


async def run_options(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    narrative: str,
    tips: list[str] | None = None,
    messages: list[Message] | None = None,
    model: str | None = None,
) -> OptionsOutput:
    """读本轮成稿(+ 黑板 + tips),出 1–3 个下一步可选项。

    messages 由调用方预构造时直接复用(把真正喂给 LLM 的完整 messages 原样存档);不传则自构造。
    model 由 orchestration 按故事内设置(options)解析后传入;None → registry 回落默认(deepseek)。
    """
    client, model_name = resolve_chat(model)
    if messages is None:
        messages = build_messages(
            "options",
            history=history,
            blackboard=blackboard,
            user_action=user_action,
            narrative=narrative,
            tips=tips,
        )

    response = await client.chat.completions.create(
        model=model_name,
        messages=messages,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OptionsError(f"JSON 解析失败: {exc}", raw) from exc
    try:
        return OptionsOutput.model_validate(data)
    except Exception as exc:  # pydantic ValidationError 等:统一包成 OptionsError
        raise OptionsError(f"Schema 校验失败: {exc}", raw) from exc
