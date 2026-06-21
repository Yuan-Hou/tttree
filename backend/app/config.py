from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore":弃用的供应商模型 key(DEEPSEEK_API_KEY / OPENAI_API_KEY / ZAI_API_KEY /
    # CLAUDE_API_KEY / GOOGLE_API_KEY)已不再用于「本站点服务」(改走 new-api,见下),也不再声明;
    # .env 里残留它们时直接忽略、不解析、不报错。真正的模型 key 配在 new-api 渠道里。
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 站点品牌名:展示为「{site_name} Tree」(留空 → 仅「Tree」)。部署时用环境变量 SITE_NAME 指定;
    # 前端从 /brand 同步后本地缓存。默认留空,不内置任何品牌字样。
    site_name: str = ""

    # 各供应商的官方 base_url / 模型名:本站点服务已不再用它们(走 new-api),仅作「自定义」模式的
    # URL 占位/候选 + 模型名常量保留。
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    # openai_image_model: str = "gpt-image-2-2026-04-21"  # 生产快照,不用滚动 alias
    openai_image_model: str = "gpt-image-2"  # 滚动 alias,指向最新可用的 openai 图像模型
    openai_base_url: str | None = None
    zai_base_url: str = "https://api.z.ai/api/paas/v4"
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    google_image_base_url: str = "https://generativelanguage.googleapis.com/v1beta/"

    # 数据库连接串。留空 → 回退到 backend/vore.db(SQLite,本地/测试默认)。部署(Docker + Postgres)
    # 时用环境变量 DATABASE_URL 指定,如 postgresql+asyncpg://user:pw@db:5432/voretree。
    # 兼容 postgres:// 与 postgresql://(会自动补成 +asyncpg 驱动)。见 app.db.session。
    database_url: str = ""

    # 全局设置(自填供应商 key)落库时的对称加密主密钥,兼作登录令牌 JWT 签名密钥。未配置 → 自填 key
    # 与登录均不可用。
    app_secret: str | None = None

    # new-api 网关(自建 LLM 中转站):「本站点服务」经它、按每用户独立 token 调模型。
    # base_url 留默认即指向自建站;admin_key 是站点管理用的 root access token(非模型 key),
    # 用于在新用户登录时自动建 new-api 子用户并取其模型 key。admin_user_id 是管理 API 必带的
    # New-Api-User 头(管理员数字 id,root 通常为 1)。
    new_api_base_url: str = "https://api.tttree.online"
    new_api_admin_key: str | None = None
    new_api_admin_user_id: str = "1"
    # new-api 额度→美元的换算系数(quota / 此值 = $)。new-api 默认 500000。若你的站点改过则相应调整。
    new_api_quota_per_unit: float = 500000.0


settings = Settings()


def brand_title() -> str:
    """品牌标题「{SITE_NAME} Tree」;SITE_NAME 留空 → 仅「Tree」。"""
    return f"{settings.site_name} Tree".strip()
