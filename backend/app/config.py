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

    # Google Gemini——文本走 OpenAI 兼容端点(/v1beta/openai/),与其余 OpenAI 兼容 provider 同路径;
    # 出图走原生 generateContent(见 imaging 适配)。base_url 默认指向官方兼容端点,可被全局设置覆盖。
    google_api_key: str | None = None
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # Gemini 出图(原生 generateContent)的接入点 base_url;与上面的「文本 OpenAI 兼容」端点不同。
    google_image_base_url: str = "https://generativelanguage.googleapis.com/v1beta/"

    # 全局设置(自填供应商 key)落库时的对称加密主密钥。未配置 → 自填 key 功能不可用(仅本站服务可用)。
    app_secret: str | None = None


settings = Settings()
