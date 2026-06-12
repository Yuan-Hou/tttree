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
