from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"

    # gpt-image-2(M3-C 起使用)。免费层不支持,需付费账户 + 组织验证。
    openai_api_key: str | None = None
    openai_image_model: str = "gpt-image-2-2026-04-21"  # 生产快照,不用滚动 alias
    # 文本/JSON agent 走 OpenAI(gpt-5.5 等)时的 base_url;留空 = OpenAI 官方默认。
    # 仅供「OpenAI 兼容」provider 用(改指自建网关等),不影响 gpt-image-2 出图客户端。
    openai_base_url: str | None = None

    # 智谱 GLM(Z.ai)——OpenAI 兼容端点,支持 response_format json_object(子步一)。
    zai_api_key: str | None = None
    zai_base_url: str = "https://api.z.ai/api/paas/v4"

    # Claude(Anthropic 原生,非 OpenAI 兼容)——适配层见子步二;此处先声明 key 字段,
    # 让已写进 .env 的 CLAUDE_API_KEY 通过校验(Settings 禁止未声明字段)。
    claude_api_key: str | None = None


settings = Settings()
