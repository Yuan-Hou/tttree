from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"

    # gpt-image-2(M3-C 起使用)。免费层不支持,需付费账户 + 组织验证。
    openai_api_key: str | None = None
    openai_image_model: str = "gpt-image-2-2026-04-21"  # 生产快照,不用滚动 alias


settings = Settings()
