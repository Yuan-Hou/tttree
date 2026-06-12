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
