"""多模型子步二:Claude(Anthropic 原生)适配。

不打真 Anthropic:用假 client 验门面路由 + 适配转换;用容错解析验「无 response_format → prompt+容错」
方案下 JSON 仍可靠;并验 claude 模型已并入清单、走同一套设置校验。
"""

import json

import pytest

from app.agents.director import run_director
from app.llm.chat import _to_anthropic, chat_json, chat_stream
from app.llm.jsonout import loads_lenient
from app.llm.registry import is_known_model, list_model_choices, provider_of, resolve_chat


# ── 容错解析 ─────────────────────────────────────────────────
def test_loads_lenient_variants():
    assert loads_lenient('{"a": 1}') == {"a": 1}                       # 干净
    assert loads_lenient('```json\n{"a": 1}\n```') == {"a": 1}         # 围栏
    assert loads_lenient('```\n{"a": 1}\n```') == {"a": 1}             # 无语言标注围栏
    assert loads_lenient('好的,这是结果:\n{"a": 1}\n以上。') == {"a": 1}  # 前后说明
    assert loads_lenient('{"a": {"b": 2}}') == {"a": {"b": 2}}         # 嵌套(截最外层)


def test_loads_lenient_raises_on_garbage():
    with pytest.raises(json.JSONDecodeError):
        loads_lenient("这根本不是 JSON")


# ── Anthropic 消息适配 ───────────────────────────────────────
def test_to_anthropic_lifts_system_and_alternates():
    msgs = [
        {"role": "system", "content": "风格圣经"},
        {"role": "system", "content": "知识库"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2a"},
        {"role": "user", "content": "u2b"},  # 相邻同角色 → 应合并
    ]
    system, convo = _to_anthropic(msgs)
    assert system == "风格圣经\n\n知识库"                       # system 提顶层并拼接
    assert [m["role"] for m in convo] == ["user", "assistant", "user"]  # 严格交替
    assert convo[-1]["content"] == "u2a\n\nu2b"               # 合并相邻 user


def test_to_anthropic_forces_leading_user():
    system, convo = _to_anthropic([{"role": "assistant", "content": "a"}])
    assert convo[0]["role"] == "user"  # 首条补一条 user 起手


# ── 门面路由:Anthropic 分支(假 client)──────────────────────
class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, blocks):
        self.content = blocks


class _StreamCtx:
    def __init__(self, parts):
        self.parts = parts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def text_stream(self):
        async def gen():
            for p in self.parts:
                yield p

        return gen()


class _Messages:
    def __init__(self, captured):
        self.captured = captured

    async def create(self, **kw):
        self.captured.update(kw)
        return _Resp([_Block('{"ok": '), _Block("1}")])  # 分块返回 → 门面应拼接

    def stream(self, **kw):
        self.captured.update(kw)
        return _StreamCtx(["第一", "二段"])


class _Client:
    def __init__(self):
        self.captured: dict = {}
        self.messages = _Messages(self.captured)


async def test_chat_json_routes_anthropic(monkeypatch):
    fake = _Client()
    monkeypatch.setattr("app.llm.chat.resolve_anthropic", lambda m: (fake, "claude-opus-4-8"))
    raw = await chat_json(
        "claude-opus-4.8",
        [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
    )
    assert raw == '{"ok": 1}'                       # 文本块拼接
    assert fake.captured["system"] == "S"           # system 走顶层
    assert "response_format" not in fake.captured   # Anthropic 无此字段
    assert fake.captured["messages"][0]["role"] == "user"


async def test_chat_stream_routes_anthropic(monkeypatch):
    fake = _Client()
    monkeypatch.setattr("app.llm.chat.resolve_anthropic", lambda m: (fake, "claude-sonnet-4-6"))
    out = [t async for t in chat_stream("claude-sonnet-4.6", [{"role": "user", "content": "U"}])]
    assert out == ["第一", "二段"]


# ── JSON agent 切 Claude:容错解析下仍可靠落地 ────────────────
async def test_director_parses_fenced_json_under_claude(monkeypatch):
    async def fake_cj(model, messages):
        assert model == "claude-opus-4.8"
        return '```json\n{"situation":"s","beat_points":["b"],"mood":"m","writing_brief":"w"}\n```'

    monkeypatch.setattr("app.agents.director.chat_json", fake_cj)
    out = await run_director([], {}, "走进门", messages=[], model="claude-opus-4.8")
    assert out.writing_brief == "w" and out.beat_points == ["b"]


# ── 清单 / 校验 ──────────────────────────────────────────────
def test_claude_models_known_and_listed():
    ids = {m["id"] for m in list_model_choices()}
    assert {"claude-opus-4.6", "claude-opus-4.8", "claude-sonnet-4.6"} <= ids
    for cid in ("claude-opus-4.6", "claude-opus-4.8", "claude-sonnet-4.6"):
        assert is_known_model(cid) and provider_of(cid) == "anthropic"


def test_resolve_chat_rejects_anthropic():
    """OpenAI 兼容路径不应被用于 Claude(门面会按 provider 分流到 resolve_anthropic)。"""
    with pytest.raises(RuntimeError):
        resolve_chat("claude-opus-4.8")
