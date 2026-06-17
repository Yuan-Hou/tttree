"""多模型接入层(故事内设置 · 子步一)。

只接「OpenAI 兼容」provider —— 都用 AsyncOpenAI 指向各自 base_url。第一批:
deepseek-v4-pro(DeepSeek base_url)、gpt-5.5(OpenAI base_url)。Anthropic SDK 以后再说。

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


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str | None  # None = SDK 默认(OpenAI 官方)
    api_key_field: str    # 取 settings 上的哪个字段作 API key


@dataclass(frozen=True)
class ModelChoice:
    id: str        # 对外暴露 / 存库的稳定标识(界面与 StorySettings 都用它)
    label: str     # 界面展示名
    provider: str  # 指向 PROVIDERS
    model: str     # 实际发给 API 的 model 名


PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider("deepseek", settings.deepseek_base_url, "deepseek_api_key"),
    "openai": Provider("openai", settings.openai_base_url, "openai_api_key"),
}

# —— 第一批可选模型清单(增改模型只动这里)——
MODEL_CHOICES: list[ModelChoice] = [
    ModelChoice("deepseek-v4-pro", "DeepSeek V4 Pro", "deepseek", settings.deepseek_model),
    ModelChoice("gpt-5.5", "GPT-5.5", "openai", "gpt-5.5"),
]

_BY_ID: dict[str, ModelChoice] = {m.id: m for m in MODEL_CHOICES}

# 全局默认模型(新故事用它,保持「全 deepseek」现状行为)
DEFAULT_MODEL_ID = "deepseek-v4-pro"

# 可单独配置模型的 agent(与 StorySettings 的 *_model 列一一对应)
AGENT_KEYS: tuple[str, ...] = ("director_a", "writer", "director_b", "options", "illustrator")


@lru_cache
def _client_for_provider(provider_name: str) -> AsyncOpenAI:
    p = PROVIDERS[provider_name]
    key = getattr(settings, p.api_key_field)
    if not key:
        raise RuntimeError(f"provider {provider_name!r} 的 API key({p.api_key_field})未配置")
    kwargs: dict = {"api_key": key}
    if p.base_url:
        kwargs["base_url"] = p.base_url
    return AsyncOpenAI(**kwargs)


def resolve_chat(model_id: str | None) -> tuple[AsyncOpenAI, str]:
    """按模型 id 取 (OpenAI 兼容 client, 实际 model 名)。

    None / 未知 id → 回落到全局默认(deepseek-v4-pro),即旧行为,保证调用方永不因配置缺失而崩。
    """
    choice = _BY_ID.get(model_id or "") or _BY_ID[DEFAULT_MODEL_ID]
    return _client_for_provider(choice.provider), choice.model


def is_known_model(model_id: str) -> bool:
    return model_id in _BY_ID


def list_model_choices() -> list[dict]:
    """供前端「故事内设置」渲染下拉。"""
    return [{"id": m.id, "label": m.label, "provider": m.provider} for m in MODEL_CHOICES]
