from functools import lru_cache

from openai import AsyncOpenAI

from app.llm.endpoints import resolve_endpoint


@lru_cache(maxsize=16)
def _client(base_url: str | None, api_key: str) -> AsyncOpenAI:
    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


def get_openai_client() -> AsyncOpenAI:
    """gpt-image-2 出图用的 OpenAI client。经 openai 接入点取 base_url + key
    (本站 .env / 全局设置自定义);按解析结果缓存,设置变更即换新 client。"""
    base_url, key = resolve_endpoint("openai")
    if not key:
        raise RuntimeError(
            "接入点 'openai' 未配置 API key(本站点服务需 .env 的 OPENAI_API_KEY,或在全局设置自填)"
        )
    return _client(base_url, key)
