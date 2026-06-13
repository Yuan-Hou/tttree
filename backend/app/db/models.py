from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Blackboard(Base):
    """当前黑板的最新状态,每个 story 一行,整存整取。"""

    __tablename__ = "blackboard"

    story_id: Mapped[str] = mapped_column(String, primary_key=True)
    json_blob: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Turn(Base):
    """逐回合存档。blackboard_after 存该轮结束后的完整黑板,用于时间回溯。"""

    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    story_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    beat_title: Mapped[str] = mapped_column(String, default="")
    user_input: Mapped[str] = mapped_column(Text, default="")
    narrative: Mapped[str] = mapped_column(Text, default="")
    director_a_json: Mapped[str] = mapped_column(Text, default="")
    director_b_json: Mapped[str] = mapped_column(Text, default="")
    blackboard_after: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class ReferenceAsset(Base):
    """用户提供的参考图库。绘图 Agent 靠 label/description 判断何时引用某张图,
    以在无 seed 的情况下锚定角色形象/物品造型的跨图一致性。"""

    __tablename__ = "reference_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    story_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)  # 语义名,如「主角立绘」
    description: Mapped[str] = mapped_column(Text, default="")  # 供 Agent 判断何时用此图
    category: Mapped[str] = mapped_column(String, default="其他")  # 角色/物品/场景氛围/其他
    file_path: Mapped[str] = mapped_column(String, nullable=False)  # 相对 backend 根
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ImageGen(Base):
    """每次出图的完整记录。与黑板 image_paths 分层:黑板存「该场景当前有哪些图」简表,
    本表存每次生成的完整出处(用了哪些参考图、最终提示词、来源入口等)。"""

    __tablename__ = "image_gens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    story_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    scene_slug: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # new_scene/variant/reuse
    final_prompt: Mapped[str] = mapped_column(Text, default="")  # 用户最终确认的提示词
    ref_asset_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON: 参考库图 id
    ref_image_paths: Mapped[str] = mapped_column(Text, default="[]")  # JSON: 历史生成图路径
    output_path: Mapped[str] = mapped_column(String, default="")  # 相对 backend 根
    origin: Mapped[str] = mapped_column(String, default="")  # director_b_proposal/user_initiated
    source_turn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
