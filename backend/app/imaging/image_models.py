"""绘图模型清单(与文本 MODEL_CHOICES 平行,但出图 API 形状不同,单列一份)。

每个绘图模型声明:对外 id / 展示名 / 走哪条出图 API(openai 兼容 images / gemini 原生 generateContent)
/ 用哪个接入点(app.llm.endpoints,决定 base_url + key 的本站/自定义)/ 实际发给 API 的 model 名。

增改绘图模型只动这份清单与 executor 的对应 API 分支。每故事的绘图模型覆盖见 StorySettings.image_model。
"""

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class ImageModel:
    id: str  # 对外 / 存库稳定标识
    label: str
    api: str  # "openai" | "gemini"
    endpoint_id: str  # 指向 app.llm.endpoints 的接入点
    model: str  # 实际发给 API 的 model 名


IMAGE_MODELS: list[ImageModel] = [
    # gpt-image-2:OpenAI images API(generate/edit)。实际 model 名取 .env 的快照 id。
    ImageModel("gpt-image-2", "gpt-image-2(OpenAI)", "openai", "openai", settings.openai_image_model),
    # Gemini 出图:原生 generateContent(responseModalities=IMAGE),参考图作 inline 图块输入。
    ImageModel(
        "gemini-3.1-flash-image", "Gemini 3.1 Flash Image", "gemini", "google_image",
        "gemini-3.1-flash-image",
    ),
]
_BY_ID: dict[str, ImageModel] = {m.id: m for m in IMAGE_MODELS}

# 全局默认绘图模型(新故事 / 未覆盖时用它,保持「gpt-image-2」现状行为)
DEFAULT_IMAGE_MODEL_ID = "gpt-image-2"


def get_image_model(model_id: str | None) -> ImageModel:
    """按 id 取绘图模型;None / 未知 → 回落默认(gpt-image-2),调用方永不因配置缺失而崩。"""
    return _BY_ID.get(model_id or "") or _BY_ID[DEFAULT_IMAGE_MODEL_ID]


def is_known_image_model(model_id: str) -> bool:
    return model_id in _BY_ID


def list_image_model_choices() -> list[dict]:
    """供前端「故事内设置 · 绘图模型」渲染下拉。"""
    return [{"id": m.id, "label": m.label, "api": m.api} for m in IMAGE_MODELS]
