from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Story(Base):
    """故事档案。数据层早带 story_id,这里把它显式建档以支持多故事管理。"""

    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # story_id
    title: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Blackboard(Base):
    """当前黑板的最新状态,每个 story 一行,整存整取。"""

    __tablename__ = "blackboard"

    story_id: Mapped[str] = mapped_column(String, primary_key=True)
    json_blob: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Knowledge(Base):
    """每个故事的「设定圣经库」:用户写的一大篇自由文本(角色人设、世界观、关系等,
    用户自己组织,不切分类条目)。一故事一行。

    与黑板的根本区别:黑板是随剧情变动的「动态世界状态」(agent 读写);知识库是用户精选的
    「恒定设定底座」(只用户写、agent 只读)。agent 永远不修改知识库。仅注入 Director-A 的上下文。
    """

    __tablename__ = "knowledge"

    story_id: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[str] = mapped_column(Text, default="")  # 一大篇自由文本
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
    # M4.5-B: 该轮三次 LLM 调用各自喂进去的「完整 messages 数组」(JSON 序列化, 原样持久化)。
    # 用途: React Flow 点节点看完整上下文、回退/重试复用历史。算完即弃 → 改为落盘。
    # 注意: 存储随轮数线性×每轮历史增长(整体≈平方);清理钩子见 app/turns/step_contexts.py
    # 的 prune_step_contexts(清理策略待定,本步只留钩子)。
    director_a_messages: Mapped[str] = mapped_column(Text, default="")
    writer_messages: Mapped[str] = mapped_column(Text, default="")
    director_b_messages: Mapped[str] = mapped_column(Text, default="")
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
