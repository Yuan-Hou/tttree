"""全局设置 · 接入点(endpoint)层。

把「某模型该用哪个 base_url + 哪个 API key」从 provider 细化到 6 个固定接入点。每个接入点两种模式:

- 本站点服务(site,默认):用 .env 里的 key + 官方/默认 base_url,用户无需填 key。
- 自定义(custom):用全局设置里用户自填的 base_url + 自填 key(落库前加密,见 app.crypto)。

接入点配置存 DB 单例行(app_settings)。启动时载入这里的内存覆盖表 _OVERRIDES;PUT 改设置后即时
刷新(见 app.global_settings_store)。registry 经 resolve_endpoint 取 (base_url, api_key),再建/复用
OpenAI / Anthropic client。内存覆盖表只在单进程内有效 —— 自托管单进程部署足够;多 worker 暂不处理。

Google 拆两个接入点:google_text(Gemini 文本,OpenAI 兼容 /v1beta/openai/)与 google_image
(Gemini 出图,原生 generateContent /v1beta/)—— 同一品牌、不同 endpoint,故配置各自独立。
"""

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class Endpoint:
    id: str
    label: str
    group: str  # UI 分组(供应商品牌)
    env_key_field: str  # 本站点服务取 .env 的哪个字段作 key
    site_base_url: str  # 本站点服务用的 base_url(读 .env override 或官方默认)
    presets: tuple[str, ...]  # 自定义时的 URL 下拉候选


def _or(value: str | None, default: str) -> str:
    return value or default


# —— 6 个固定接入点(顺序即 UI 展示顺序)——
ENDPOINTS: list[Endpoint] = [
    Endpoint(
        "deepseek", "DeepSeek", "DeepSeek", "deepseek_api_key",
        settings.deepseek_base_url, (settings.deepseek_base_url,),
    ),
    Endpoint(
        "openai", "OpenAI(含 gpt-image-2 出图)", "OpenAI", "openai_api_key",
        _or(settings.openai_base_url, "https://api.openai.com/v1"),
        ("https://api.openai.com/v1",),
    ),
    Endpoint(
        "zai", "Z.ai(智谱)", "Z.ai", "zai_api_key",
        settings.zai_base_url, (settings.zai_base_url,),
    ),
    Endpoint(
        "anthropic", "Claude", "Anthropic", "claude_api_key",
        "https://api.anthropic.com", ("https://api.anthropic.com",),
    ),
    Endpoint(
        "google_text", "Google · 文本(Gemini)", "Google", "google_api_key",
        settings.google_base_url,
        ("https://generativelanguage.googleapis.com/v1beta/openai/",),
    ),
    Endpoint(
        "google_image", "Google · 出图(Gemini Image)", "Google", "google_api_key",
        settings.google_image_base_url,
        ("https://generativelanguage.googleapis.com/v1beta/",),
    ),
]
_BY_ID: dict[str, Endpoint] = {e.id: e for e in ENDPOINTS}
ENDPOINT_IDS: tuple[str, ...] = tuple(e.id for e in ENDPOINTS)


def get_endpoint(endpoint_id: str) -> Endpoint:
    return _BY_ID[endpoint_id]


def is_known_endpoint(endpoint_id: str) -> bool:
    return endpoint_id in _BY_ID


@dataclass(frozen=True)
class Override:
    base_url: str
    api_key: str  # 明文(已解密);仅存内存,不落库


# 内存覆盖表:仅含 custom 模式的接入点。site 模式不入表 → resolve 回落 .env。
_OVERRIDES: dict[str, Override] = {}


def set_overrides(overrides: dict[str, Override]) -> None:
    """整表替换(启动载入 / PUT 后刷新)。site 模式的接入点不应出现在 overrides 里。"""
    _OVERRIDES.clear()
    _OVERRIDES.update(overrides)


def resolve_endpoint(endpoint_id: str) -> tuple[str | None, str | None]:
    """接入点 → (base_url, api_key)。custom 用用户配置;否则用 .env + 默认 URL。"""
    ov = _OVERRIDES.get(endpoint_id)
    if ov is not None:
        return ov.base_url, ov.api_key
    ep = _BY_ID[endpoint_id]
    return ep.site_base_url, getattr(settings, ep.env_key_field, None)


def endpoint_mode(endpoint_id: str) -> str:
    return "custom" if endpoint_id in _OVERRIDES else "site"
