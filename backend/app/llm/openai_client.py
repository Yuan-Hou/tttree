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
    (本站点服务=new-api 用户 token / 全局设置自定义);按解析结果缓存,设置变更即换新 client。"""
    base_url, key = resolve_endpoint("openai")
    if not key:
        raise RuntimeError(
            "接入点 'openai' 无可用 key:本站点服务的 new-api 模型 key 尚未就绪"
            "(重新登录可自动补齐),或在全局设置切到「自定义」填自己的 key"
        )
    return _client(base_url, key)
