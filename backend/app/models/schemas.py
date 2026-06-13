from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WritingBrief(BaseModel):
    """Director -> Writer 创作指令。extra='allow' 以便 Director 按需附加字段(如 continuity_notes)。"""

    model_config = ConfigDict(extra="allow")

    must_include: list[str] = Field(default_factory=list)
    mood: str
    focus: str
    pov: str
    length_hint: str


class DirectorOutput(BaseModel):
    beat: str
    scene_event: Literal["enter_new", "modify_current", "recall", "stay"]
    scene_id: str
    scene_delta: dict[str, Any] = Field(default_factory=dict)
    character_updates: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mood: str
    writing_brief: WritingBrief
    choices: list[str] = Field(default_factory=list)


class ReferenceRef(BaseModel):
    """提示词稿引用清单的一项:语义名 → 实际图(参考库 asset_id 或历史图 path)。"""

    model_config = ConfigDict(extra="allow")

    semantic_name: str  # 语义名(参考库图=label;历史图=场景名+时序状态),绝不用位置序号
    source: Literal["reference_asset", "history_image"]
    asset_id: int | None = None  # source=reference_asset 时填
    image_path: str | None = None  # source=history_image 时填
    purpose: str  # 这张图负责提供什么(用途说明)


class IllustratorDraft(BaseModel):
    """绘图 Agent 的提示词稿:连贯文本 + 引用清单。"""

    model_config = ConfigDict(extra="allow")

    kind: Literal["new_scene", "variant", "reuse"]
    prompt_text: str
    reference_manifest: list[ReferenceRef] = Field(default_factory=list)
