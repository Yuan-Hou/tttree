from functools import lru_cache

from openai import AsyncOpenAI

from app.config import settings


@lru_cache
def get_openai_client() -> AsyncOpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY 未配置(gpt-image-2 需付费账户 + 组织验证)")
    return AsyncOpenAI(api_key=settings.openai_api_key)
