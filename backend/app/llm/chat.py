"""LLM 调用门面(子步二):把「OpenAI 兼容」与「Anthropic 原生」两条路径收敛成统一接口,
让各 agent 不必感知 provider 差异。

- chat_json(model, messages) -> str:产 JSON 的一次性调用,返回助手原始文本(调用方再容错解析)。
  OpenAI 兼容走 response_format={"type":"json_object"};Anthropic 无此字段,靠 prompt 强约束。
- chat_stream(model, messages) -> AsyncIterator[str]:流式纯文本(Writer 用)。

Anthropic 适配三处差异:① system 从 messages 提到顶层 system 参数;② user/assistant 必须严格
交替且首条为 user(合并相邻同角色、必要时补一条 user 起手);③ 流式走 messages.stream 的
text_stream。build_messages 前缀结构与 OpenAI 兼容侧的缓存红利均不受影响(本门面不改 messages 本身,
只在送 Anthropic 时做等价转换)。Anthropic prompt caching 暂不做 —— 走 Claude 即全价(已接受)。
"""

from collections.abc import AsyncIterator

from app.llm.registry import ANTHROPIC_PROVIDER, provider_of, resolve_anthropic, resolve_chat

# Anthropic 要求显式 max_tokens。给足:JSON 产出与单轮叙事都够用。
ANTHROPIC_MAX_TOKENS = 8192


def _to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI 风格 messages → (system 文本, 交替的 user/assistant 列表)。"""
    system_parts: list[str] = []
    convo: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            system_parts.append(m.get("content", ""))
            continue
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = m.get("content", "")
        if convo and convo[-1]["role"] == role:  # 合并相邻同角色,满足严格交替
            convo[-1]["content"] += "\n\n" + content
        else:
            convo.append({"role": role, "content": content})
    if not convo or convo[0]["role"] != "user":  # 首条须为 user
        convo.insert(0, {"role": "user", "content": "(开始)"})
    return "\n\n".join(system_parts), convo


async def chat_json(model: str | None, messages: list[dict]) -> str:
    """产 JSON 的一次性调用,返回助手原始文本。"""
    if provider_of(model) == ANTHROPIC_PROVIDER:
        client, name = resolve_anthropic(model)
        system, convo = _to_anthropic(messages)
        resp = await client.messages.create(
            model=name, system=system, messages=convo, max_tokens=ANTHROPIC_MAX_TOKENS
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")

    client, name = resolve_chat(model)
    resp = await client.chat.completions.create(
        model=name, messages=messages, response_format={"type": "json_object"}
    )
    return resp.choices[0].message.content or ""


async def chat_stream(model: str | None, messages: list[dict]) -> AsyncIterator[str]:
    """流式纯文本(逐 token yield)。"""
    if provider_of(model) == ANTHROPIC_PROVIDER:
        client, name = resolve_anthropic(model)
        system, convo = _to_anthropic(messages)
        async with client.messages.stream(
            model=name, system=system, messages=convo, max_tokens=ANTHROPIC_MAX_TOKENS
        ) as stream:
            async for text in stream.text_stream:
                yield text
        return

    client, name = resolve_chat(model)
    stream = await client.chat.completions.create(model=name, messages=messages, stream=True)
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
