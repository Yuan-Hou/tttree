"""Options agent:Writer 成稿后,出 1–3 个「下一步可选项」给玩家挑。

编排上是 Director-B 的并行兄弟(同读本轮之前的黑板 + 历史,易变区尾部各附 Writer 成稿与 tips),
但**互不依赖、互不影响**:Options 是叶子(无下游),reducer 只等 B,Options 失败不阻断落盘。
复用 build_messages 统一入口,前缀(system 文风圣经 + history)与 A/Writer/B 逐字节一致,缓存命中。
"""

import json
from collections.abc import AsyncIterator

from app.agents.context import Blackboard, Message, build_messages
from app.llm.chat import chat_json_stream
from app.llm.jsonout import loads_lenient
from app.models.schemas import OptionsOutput


class OptionsError(Exception):
    """Options 返回内容无法解析/校验时抛出,携带原始响应文本便于调试。"""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def parse_options_output(raw: str) -> OptionsOutput:
    """把 LLM 原始文本解析+校验为 OptionsOutput(失败抛 OptionsError,携带 raw)。
    一次性路径与流式路径共用,容错语义一致。"""
    try:
        data = loads_lenient(raw)
    except json.JSONDecodeError as exc:
        raise OptionsError(f"JSON 解析失败: {exc}", raw) from exc
    try:
        return OptionsOutput.model_validate(data)
    except Exception as exc:  # pydantic ValidationError 等:统一包成 OptionsError
        raise OptionsError(f"Schema 校验失败: {exc}", raw) from exc


def build_options_messages(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    narrative: str,
    tips: list[str] | None = None,
) -> list[Message]:
    return build_messages(
        "options", history=history, blackboard=blackboard, user_action=user_action,
        narrative=narrative, tips=tips,
    )


async def stream_options(
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    narrative: str,
    tips: list[str] | None = None,
    messages: list[Message] | None = None,
    model: str | None = None,
) -> AsyncIterator[str]:
    """逐 token 流式产 Options 的原始 JSON 文本(调用方累积后用 parse_options_output 解析)。"""
    if messages is None:
        messages = build_options_messages(history, blackboard, user_action, narrative, tips)
    async for delta in chat_json_stream(model, messages):
        yield delta
