"""全局设置 · 接入点(endpoint)层。

把「某模型该用哪个 base_url + 哪个 API key」从 provider 细化到 6 个固定接入点。每个接入点两种模式:

- 本站点服务(site,默认):用 .env 里的 key + 官方/默认 base_url,用户无需填 key。
- 自定义(custom):用全局设置里用户自填的 base_url + 自填 key(落库前加密,见 app.crypto)。

接入点配置**按用户**存 DB(app_settings,每用户一行)。启动时逐用户载入内存覆盖表 _OVERRIDES;
某用户 PUT 改设置后即时刷新其那份(见 app.global_settings_store)。registry 经 resolve_endpoint 取
(base_url, api_key),再建/复用 OpenAI / Anthropic client。内存覆盖表只在单进程内有效 ——
自托管单进程部署足够;多 worker 暂不处理。

Google 拆两个接入点:google_text(Gemini 文本,OpenAI 兼容 /v1beta/openai/)与 google_image
(Gemini 出图,原生 generateContent /v1beta/)—— 同一品牌、不同 endpoint,故配置各自独立。
"""

from dataclasses import dataclass

from app.auth.context import current_uid
from app.config import settings


@dataclass(frozen=True)
class Endpoint:
    id: str
    label: str
    group: str  # UI 分组(供应商品牌)
    site_base_url: str  # 官方默认 base_url(仅作「自定义」模式的占位/候选;本站点服务已不再用它)
    presets: tuple[str, ...]  # 自定义时的 URL 下拉候选
    new_api_suffix: str  # 「本站点服务」经 new-api 时,接在 NEW_API_BASE_URL 后的路径段


def _or(value: str | None, default: str) -> str:
    return value or default


# —— 6 个固定接入点(顺序即 UI 展示顺序)——
# new_api_suffix:经 new-api 网关时接在 NEW_API_BASE_URL 后的路径。OpenAI 兼容文本 + gpt-image-2 出图
# 都走 /v1(new-api 按模型名路由);anthropic 原生用空(SDK 自动接 /v1/messages);gemini 原生出图走 /v1beta/。
ENDPOINTS: list[Endpoint] = [
    Endpoint(
        "deepseek", "DeepSeek", "DeepSeek",
        settings.deepseek_base_url, (settings.deepseek_base_url,), "/v1",
    ),
    Endpoint(
        "openai", "OpenAI(含 gpt-image-2 出图)", "OpenAI",
        _or(settings.openai_base_url, "https://api.openai.com/v1"),
        ("https://api.openai.com/v1",), "/v1",
    ),
    Endpoint(
        "zai", "Z.ai(智谱)", "Z.ai",
        settings.zai_base_url, (settings.zai_base_url,), "/v1",
    ),
    Endpoint(
        "anthropic", "Claude", "Anthropic",
        "https://api.anthropic.com", ("https://api.anthropic.com",), "",
    ),
    Endpoint(
        "google_text", "Google · 文本(Gemini)", "Google",
        settings.google_base_url,
        ("https://generativelanguage.googleapis.com/v1beta/openai/",), "/v1",
    ),
    Endpoint(
        "google_image", "Google · 出图(Gemini Image)", "Google",
        settings.google_image_base_url,
        ("https://generativelanguage.googleapis.com/v1beta/",), "/v1beta/",
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


# 内存覆盖表:按用户隔离。user_id → {endpoint_id → Override}。仅含该用户 custom 模式的接入点;
# site 模式不入表 → resolve 回落 .env。哪个用户在调,经 current_uid(请求级 contextvar)得知;
# 后台回合/绘图作业用 create_task 派生 → 继承请求的 uid 快照,故深处解析自然带上正确用户。
_OVERRIDES: dict[str, dict[str, Override]] = {}


def set_user_overrides(user_id: str, overrides: dict[str, Override]) -> None:
    """整表替换某用户的覆盖(启动逐用户载入 / 该用户 PUT 后刷新)。空 → 摘除该用户(全回落本站)。"""
    if overrides:
        _OVERRIDES[user_id] = dict(overrides)
    else:
        _OVERRIDES.pop(user_id, None)


def clear_all_overrides() -> None:
    """清空所有用户的覆盖(测试复位 / 全量重载前)。"""
    _OVERRIDES.clear()


def _overrides_for(user_id: str | None) -> dict[str, Override]:
    return _OVERRIDES.get(user_id, {}) if user_id is not None else {}


# ── 本站点服务的 new-api 模型 key(按用户隔离)──
# 「本站点服务」只经 new-api、用该用户在 new-api 的专属 token;**不再回落 .env 官方 key**
# (用户无权用 .env 里的供应商 key)。token 由 app.newapi 在用户登录时惰性补齐并载入这里;
# 启动时从库全量载入。无 token(未补齐 / new-api 宕机)→ resolve 返回 key=None,调用方据此降级报错。
_SITE_KEYS: dict[str, str] = {}


def set_user_site_key(user_id: str, api_key: str) -> None:
    if api_key:
        _SITE_KEYS[user_id] = api_key
    else:
        _SITE_KEYS.pop(user_id, None)


def clear_all_site_keys() -> None:
    _SITE_KEYS.clear()


def has_site_key(user_id: str | None) -> bool:
    return user_id is not None and user_id in _SITE_KEYS


def new_api_base_for(endpoint_id: str) -> str:
    """该接入点经 new-api 时的 base_url = NEW_API_BASE_URL + 接入点路径段。"""
    ep = _BY_ID[endpoint_id]
    return (settings.new_api_base_url or "").rstrip("/") + ep.new_api_suffix


def resolve_endpoint(
    endpoint_id: str, user_id: str | None = None
) -> tuple[str | None, str | None]:
    """接入点 → (base_url, api_key)。

    - 该用户对此接入点设了「自定义」→ 用其自填 base_url + key(允许用户接自己的网关)。
    - 否则「本站点服务」→ 经 new-api 网关 + 该用户的 new-api token。无 token → key=None(调用方报错)。
      不回落 .env 官方 key。user_id 省略时取 current_uid(请求/作业上下文,后台作业经 create_task 继承)。
    """
    uid = user_id if user_id is not None else current_uid.get()
    ov = _overrides_for(uid).get(endpoint_id)
    if ov is not None:
        return ov.base_url, ov.api_key
    return new_api_base_for(endpoint_id), (_SITE_KEYS.get(uid) if uid is not None else None)


def endpoint_mode(endpoint_id: str, user_id: str | None = None) -> str:
    uid = user_id if user_id is not None else current_uid.get()
    return "custom" if endpoint_id in _overrides_for(uid) else "site"
