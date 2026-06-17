from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# scene_intent 的合法取值;模型偶尔输出枚举外的词时不报错,代码侧把无法识别的当 uncertain
VALID_SCENE_INTENTS = ("stay", "likely_new_scene", "likely_recall", "uncertain")


def normalize_scene_intent(value: str | None) -> str:
    return value if value in VALID_SCENE_INTENTS else "uncertain"


class DirectorOutput(BaseModel):
    """Director-A 的「开拍前导演草稿」:只产出供阅读的创作引导与轻结构意图,绝不产出状态。

    Schema 原则(从 character_updates 的 dict_type 失败吸取的教训):A 的输出走**宽松容错**
    路线——供阅读的字段一律用宽松类型(str / Optional),不用严格 Enum、不用嵌套 dict,以免
    模型把本该是散文的内容塞进结构时校验失败、打断整轮。`extra="ignore"`:模型多输出的字段
    (如旧的 character_updates / scene_delta)一律静默忽略,不报错。A 仍用 JSON mode + 低温,
    但不再拿严格类型当硬闸门去打断回合。

    状态的唯一权威是 Director-B 的全量重写;A 不碰任何角色/场景/物品状态。

    字段顺序即思考顺序(确定→思考→总结):DeepSeek JSON mode 实测会按 schema 字段顺序逐字段
    生成,后字段能用到前字段已落定的值(见 scripts/diag_field_order.py:8/8)。所以这里把字段排成
    一条思维链——先锚定确定的情境,再在其上推演情节要点与情绪,最后凝练出深思熟虑的 writing_brief。
    """

    model_config = ConfigDict(extra="ignore")

    # ① 确定性字段:先钉地基,锚定当前事实(后续字段在此之上推演)
    situation: str  # 当前情境 + 玩家这轮要推进的方向(简述,锚定事实,不展开叙事)
    # A 对场景走向的非权威意图猜测:stay / likely_new_scene / likely_recall / uncertain
    scene_intent: str = "uncertain"
    # 若觉得要进新/回旧场景,用自然语言点出哪个/什么样(纯提示,不要求精确 slug)
    scene_hint: str = ""
    # ② 思考性字段:在确定地基上推演——这是解决「围着单点打转」的核心
    # 这一段要依次经过的情节要点,覆盖到「下一个自然场景/事件节点」为止:既是推进路标、也是推进边界。
    beat_points: list[str] = Field(default_factory=list)
    mood: str = ""  # 情绪基调走向(可含变化)
    # 详略/节奏判断:这一拍整体是「日常过渡(朴素带过)」还是「关键时刻(可多给笔墨)」,
    # 可顺带点出 beat_points 里哪些点该略写、哪些值得展开。详略由 A 每拍动态判断,而非圣经一刀切。
    pacing: str = ""
    # ③ 总结性字段:把以上凝练成给下游的产物(此时已是深思熟虑的结果)
    writing_brief: str  # 综合以上的最终创作指引,压平为单一自然语言段(融合视角/详略节奏/须含要点/篇幅)
    # ④ 设定提示(放最后:A 想清 brief/beat 后再决定下传哪些设定):从它独有的知识库里摘取本轮情节
    # 与绘图都可能用到的相关设定(世界观、角色性格/外观等),下传给看不到知识库的 Writer/B/Options/绘图写稿。
    tips: list[str] = Field(default_factory=list)


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
