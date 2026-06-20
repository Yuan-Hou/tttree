from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
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
    # Options agent(Writer 后与 B 并行的叶子):它的输出(OptionsOutput JSON)+ 喂进去的完整 messages。
    # 与 director_*_messages 同性质(显微镜可看、回退/重试复用)。Options 失败/老数据 → 空串。
    options_json: Mapped[str] = mapped_column(Text, default="")
    options_messages: Mapped[str] = mapped_column(Text, default="")
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
    # 取代:同场景同轮次内被后续重绘取代的旧正典图 → True。退出绘图 Agent 候选池(对 Agent 隐身),
    # 但仍留在黑板 image_paths(gallery 可翻页)、仍可在 RefPicker 手动选(与 user_initiated 同等待遇)。
    superseded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class StorySettings(Base):
    """故事内设置(故事内设置里程碑 · 子步一):每故事一行,随 fork 复制、随 delete 清理。

    模型设置:default_model = 全局默认模型 id;各 agent 的 *_model 为「覆盖」——空串表示
    「用全局默认」。调用各 agent 时按「该 agent 覆盖 → 否则全局默认」决定用哪个模型。
    模型 id 取值见 app/llm/registry.MODEL_CHOICES。新故事默认全部走 deepseek-v4-pro,
    即旧行为不变。default 这里写死 'deepseek-v4-pro' 以避免 models 反向依赖 registry;
    与 registry.DEFAULT_MODEL_ID 保持一致(store 层建行时也用 DEFAULT_MODEL_ID)。
    """

    __tablename__ = "story_settings"

    story_id: Mapped[str] = mapped_column(String, primary_key=True)
    default_model: Mapped[str] = mapped_column(String, default="deepseek-v4-pro")
    director_a_model: Mapped[str] = mapped_column(String, default="")  # 空 = 用全局默认
    writer_model: Mapped[str] = mapped_column(String, default="")
    director_b_model: Mapped[str] = mapped_column(String, default="")
    options_model: Mapped[str] = mapped_column(String, default="")
    illustrator_model: Mapped[str] = mapped_column(String, default="")
    # 故事内自定义圣经(bible 子步):空串 = 用全局打包默认。文风圣经=叙事 system 前缀,
    # 画风圣经=illustrator 易变区。整篇覆盖,随 fork 复制、随 delete 清理。
    style_bible: Mapped[str] = mapped_column(Text, default="")
    visual_style_bible: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class DrawProposal(Base):
    """绘图待办(M5-B 绘图语义升级):把 Director-B 的瞬时 draw_proposals 升级成「积压在场景上、
    跨轮可见可画」的持久待办。绘图的发起与场景诞生解耦——第2轮的提案可能拖到第5轮才画。

    kind 由**后端按场景诞生点权威判定**(不按发起轮):提案产生轮 == 场景 origin_turn → new_scene;
    > origin_turn(场景已存在,后续再画/召回) → variant。一个场景一生只有诞生轮那条是 new_scene。
    回退/重试某轮时,该轮产生的提案随之清理(依附的轮没了),由 reducer 重新落库。
    """

    __tablename__ = "draw_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    story_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    scene_slug: Mapped[str] = mapped_column(String, nullable=False)
    origin_proposal_turn: Mapped[int] = mapped_column(Integer, nullable=False)  # 这条提案在哪轮产生
    kind: Mapped[str] = mapped_column(String, nullable=False)  # new_scene/variant(后端按 origin_turn 定)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/done
    reason: Mapped[str] = mapped_column(Text, default="")  # B 给的配图理由,供待办面板展示
    done_image_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 画完指向 ImageGen.id
    # 写稿步(绘图 Agent / DeepSeek)的持久产物 —— 让「写稿节点」与「画图节点」成为两个真正独立、
    # 各自可看可重试的步骤。draft_messages=喂给绘图 Agent 的完整输入(按区块展示+可编辑);
    # draft_prompt=它写出的提示词文本(写稿的输出,绝不是图);draft_manifest=它建议的引用清单。
    draft_messages: Mapped[str] = mapped_column(Text, default="")  # JSON messages
    draft_prompt: Mapped[str] = mapped_column(Text, default="")  # 写稿输出:提示词文本
    draft_manifest: Mapped[str] = mapped_column(Text, default="[]")  # JSON: 建议的 ReferenceRef
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
