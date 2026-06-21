"""多模型接入层(故事内设置 · 子步一)。

只接「OpenAI 兼容」provider —— 都用 AsyncOpenAI 指向各自 base_url。已接:
deepseek-v4-pro(DeepSeek)、gpt-5.5(OpenAI)、glm-5.1 / glm-5.2(智谱 Z.ai)。
Z.ai 端点已核实为 OpenAI 兼容且支持 response_format json_object,故与上面同路径,
无需适配层。Anthropic(Claude)非 OpenAI 兼容,走单独适配路径(子步二)。

设计要点:
- 可选模型清单是**数据**(MODEL_CHOICES),增改模型只改这份清单,不动调用逻辑。
- 每 provider 配自己的 base_url + 取哪个 settings 字段作 key。
- resolve_chat(model_id) → (client, 实际 model 名),供各 agent 统一取用。

JSON mode 兼容:Director-A/B 与绘图写稿用 response_format={"type":"json_object"};
DeepSeek 与 OpenAI(gpt-5.5)都支持该字段,二者都要求 prompt 里出现「json」字样
(我们的 system 提示已要求输出 JSON),故切模型后 JSON 输出仍可被 json.loads 可靠解析。
Writer 是纯文本流,无此约束。

缓存代价(已知并接受,不优化):DeepSeek 的前缀缓存是 provider 侧、按稳定前缀命中的红利。
切到非 deepseek 的 agent 后,该 agent 的全量历史会全价重发、拿不到这份缓存红利——这是
多模型自由的代价,用户已接受,这里仅注释说明,不做特殊处理。
"""

from dataclasses import dataclass
from functools import lru_cache

from openai import AsyncOpenAI

from app.config import settings
from app.llm.endpoints import resolve_endpoint


@dataclass(frozen=True)
class ModelChoice:
    id: str        # 对外暴露 / 存库的稳定标识(界面与 StorySettings 都用它)
    label: str     # 界面展示名
    provider: str  # provider 名(经 _PROVIDER_ENDPOINT 映射到接入点;见 app.llm.endpoints)
    model: str     # 实际发给 API 的 model 名


# 各模型的 base_url + API key 不再由本模块直接持有,而是经接入点层(app.llm.endpoints)按
# 「本站点服务(.env) / 自定义(全局设置)」解析。本模块只保留 provider→endpoint 映射与 client 构建。

# —— 可选模型清单(增改模型只动这里)——
# GLM 的对外 id 即发给 API 的 model 名;若 Z.ai 实际模型名有出入,只改这两行的最后一个参数。
MODEL_CHOICES: list[ModelChoice] = [
    ModelChoice("deepseek-v4-pro", "DeepSeek V4 Pro", "deepseek", settings.deepseek_model),
    ModelChoice("gpt-5.5", "GPT-5.5", "openai", "gpt-5.5"),
    ModelChoice("glm-5.1", "GLM-5.1", "zai", "glm-5.1"),
    ModelChoice("glm-5.2", "GLM-5.2", "zai", "glm-5.2"),
    # Claude:Anthropic 原生(非 OpenAI 兼容),走 app/llm/chat.py 的适配路径。对外 id 用 4.6/4.8
    # 点号写法,API model 名用连字符。无 response_format → JSON 靠 prompt 强约束 + 容错解析。
    # 缓存代价:Anthropic prompt caching 暂不做,走 Claude 即全价全量重发(已接受,不优化)。
    ModelChoice("claude-opus-4.6", "Claude Opus 4.6", "anthropic", "claude-opus-4-6"),
    ModelChoice("claude-opus-4.8", "Claude Opus 4.8", "anthropic", "claude-opus-4-8"),
    ModelChoice("claude-sonnet-4.6", "Claude Sonnet 4.6", "anthropic", "claude-sonnet-4-6"),
    # Google Gemini 文本(OpenAI 兼容):对外 id 即发给 API 的 model 名。response_format json_object
    # 与流式均受支持,故走与 DeepSeek/OpenAI 同一调用路径;JSON 仍靠现有 prompt「json」约束可靠解析。
    ModelChoice("gemini-3.5-flash", "Gemini 3.5 Flash", "google", "gemini-3.5-flash"),
    ModelChoice("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", "google", "gemini-3.1-flash-lite"),
    ModelChoice("gemini-3.1-pro-preview", "Gemini 3.1 Pro (Preview)", "google", "gemini-3.1-pro-preview"),
]

_BY_ID: dict[str, ModelChoice] = {m.id: m for m in MODEL_CHOICES}

# 全局默认模型(新故事用它,保持「全 deepseek」现状行为)
DEFAULT_MODEL_ID = "deepseek-v4-pro"

# 可单独配置模型的 agent(与 StorySettings 的 *_model 列一一对应)
AGENT_KEYS: tuple[str, ...] = ("director_a", "writer", "director_b", "options", "illustrator")


# provider → 接入点 id(google 文本走 google_text;出图 google_image 由 imaging 侧另取)。
_PROVIDER_ENDPOINT: dict[str, str] = {
    "deepseek": "deepseek",
    "openai": "openai",
    "zai": "zai",
    "anthropic": "anthropic",
    "google": "google_text",
}


@lru_cache(maxsize=32)
def _openai_client(base_url: str | None, api_key: str) -> AsyncOpenAI:
    """按 (base_url, api_key) 复用 client。全局设置改 endpoint/key → 解析出不同二元组 → 自然换新 client,
    旧的留在缓存里不再命中(数量有界,可接受);故 _client_for_provider 本身不再缓存。"""
    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


def _client_for_provider(provider_name: str) -> AsyncOpenAI:
    endpoint_id = _PROVIDER_ENDPOINT[provider_name]
    base_url, key = resolve_endpoint(endpoint_id)
    if not key:
        raise RuntimeError(
            f"接入点 {endpoint_id!r} 无可用 key:本站点服务的 new-api 模型 key 尚未就绪"
            "(重新登录可自动补齐),或在全局设置切到「自定义」填自己的 key"
        )
    return _openai_client(base_url, key)


def resolve_chat(model_id: str | None) -> tuple[AsyncOpenAI, str]:
    """按模型 id 取 (OpenAI 兼容 client, 实际 model 名)。

    None / 未知 id → 回落到全局默认(deepseek-v4-pro),即旧行为,保证调用方永不因配置缺失而崩。
    仅供 OpenAI 兼容 provider;Anthropic 走 resolve_anthropic(由 app/llm/chat.py 按 provider 分流)。
    """
    choice = _BY_ID.get(model_id or "") or _BY_ID[DEFAULT_MODEL_ID]
    if choice.provider == ANTHROPIC_PROVIDER:
        raise RuntimeError(f"{choice.id} 是 Anthropic 模型,请走 resolve_anthropic / chat 适配层")
    return _client_for_provider(choice.provider), choice.model


ANTHROPIC_PROVIDER = "anthropic"


def provider_of(model_id: str | None) -> str:
    """该模型 id 属于哪个 provider(未知 → 默认模型的 provider)。chat 适配层据此分流。"""
    return (_BY_ID.get(model_id or "") or _BY_ID[DEFAULT_MODEL_ID]).provider


@lru_cache(maxsize=8)
def _anthropic_client_for(base_url: str | None, api_key: str):
    """AsyncAnthropic(惰性导入 SDK)。按 (base_url, api_key) 复用,理由同 _openai_client。"""
    from anthropic import AsyncAnthropic  # 惰性:不接 Claude 的部署不强依赖该包

    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncAnthropic(**kwargs)


def _anthropic_client():
    base_url, key = resolve_endpoint("anthropic")
    if not key:
        raise RuntimeError(
            "接入点 'anthropic' 无可用 key:本站点服务的 new-api 模型 key 尚未就绪"
            "(重新登录可自动补齐),或在全局设置切到「自定义」填自己的 key"
        )
    return _anthropic_client_for(base_url, key)


def resolve_anthropic(model_id: str | None):
    """按模型 id 取 (AsyncAnthropic client, 实际 model 名)。仅供 Anthropic 模型。"""
    choice = _BY_ID.get(model_id or "") or _BY_ID[DEFAULT_MODEL_ID]
    return _anthropic_client(), choice.model


def is_known_model(model_id: str) -> bool:
    return model_id in _BY_ID


def list_model_choices() -> list[dict]:
    """供前端「故事内设置」渲染下拉。"""
    return [{"id": m.id, "label": m.label, "provider": m.provider} for m in MODEL_CHOICES]
