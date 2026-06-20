"""共享上下文构造。

缓存命中的唯一保障:所有 agent 都必须经由 build_messages() 构造 messages,禁止各自拼装。
布局严格如下(M2 起易变区由「世界状态快照」替换为「完整黑板」,位置不变):

  [system]  仅文风圣经(几乎不变,所有 agent 共用,Director 忽略其文学要求)
  [history] 全量对话历史,原生 user/assistant 交替,只追加不修改;
            user = 玩家原始输入,assistant = Writer 写出的叙事(均为「干净」消息)
  [user]    最后一条 user 消息,承载易变区,顺序固定:
              1. 完整黑板(所有 agent 共享的唯一世界真相)
              2. 本轮玩家实际输入
              3. 该 agent 的任务指令(按 agent_role 区分;Director-B 尾部再附 Writer 成稿)

system / history / 黑板 / 玩家输入这一段前缀对三个 agent 完全一致,只有尾部任务指令不同。
这样后续 agent 可复用前面 agent 调用的全部前缀缓存。

严禁在 system / history / 黑板中插入时间戳、随机 ID 或任何会变动的稳定前缀内容。
"""

import json
from typing import Any, Literal

from app.agents.bibles import DEFAULT_STYLE_BIBLE
from app.agents.loader import load_prompt

AgentRole = Literal["director", "writer", "director_review", "options", "illustrator"]

Message = dict[str, str]
Blackboard = dict[str, Any]

# 默认文风圣经(全局打包)。每故事可在设置里自定义,届时由调用方把生效值经 build_messages(style_bible=...)
# 传入覆盖这份默认 —— 仍是 system 前缀首条,缓存铁律不破(各故事前缀本就独立)。
STYLE_BIBLE = DEFAULT_STYLE_BIBLE
DIRECTOR_TASK = load_prompt("director_task.md")
WRITER_TASK = load_prompt("writer_task.md")
DIRECTOR_REVIEW_TASK = load_prompt("director_review_task.md")
OPTIONS_TASK = load_prompt("options_task.md")
ILLUSTRATOR_TASK = load_prompt("illustrator_task.md")


def _render_knowledge(knowledge: str) -> str:
    """A 专属的设定参考块。作为第二条 system 消息,放在文风圣经之后、history 之前的
    稳定前缀位置——基本不变,故 A 的历史缓存大部分仍命中,仅用户改知识库时击穿一次。"""
    return f"【本故事的世界与角色设定参考(world / character bible)】\n{knowledge}"


# 系统管理、agent 既不需要也不被信任的场景字段:出图流程维护 image_paths、reducer 维护 origin_turn。
# 它们对各叙事 agent 是噪声(image_paths 是文件路径、origin_turn 是审计轮号),且 B 的回显从不被采信
# (reducer 落库时按 slug 权威加回)。故统一从「发给 LLM 的黑板视图」里剥离,不进任何 agent 的上下文。
_SYSTEM_SCENE_FIELDS = ("image_paths", "origin_turn")


def _blackboard_view(blackboard: Blackboard) -> Blackboard:
    """发给各 agent 的黑板「视图」:剥离 _SYSTEM_SCENE_FIELDS,并去掉 story_meta.title。
    只影响渲染给 LLM 的内容,不改持久黑板(原 dict 不被修改);reducer 落库时再把系统字段权威加回。

    标题只是档案标记、不参与故事 → 不进任何 agent 的黑板视图(story_meta 其余字段照常保留)。
    这里仍做剥离是为兼容历史库:老黑板里可能还存着 story_meta.title。"""
    view: Blackboard = dict(blackboard)
    sm = blackboard.get("story_meta")
    if isinstance(sm, dict) and "title" in sm:
        view["story_meta"] = {k: v for k, v in sm.items() if k != "title"}
    scenes = blackboard.get("scenes")
    if isinstance(scenes, dict):
        view["scenes"] = {
            slug: (
                {k: v for k, v in sc.items() if k not in _SYSTEM_SCENE_FIELDS}
                if isinstance(sc, dict)
                else sc
            )
            for slug, sc in scenes.items()
        }
    return view


def _render_blackboard(blackboard: Blackboard) -> str:
    """渲染易变区的黑板视图(已剥离系统管理字段)。同一回合内三个 agent 必须收到逐字节相同的
    黑板,因此本函数是纯函数,不引入任何随机/时间内容。"""
    body = json.dumps(_blackboard_view(blackboard), ensure_ascii=False, indent=2)
    return f"【当前黑板】\n{body}"


def _render_tips(tips: list[str] | None) -> str:
    """Director-A 摘给下游的「本轮相关设定提示」。只有 A 能看知识库,tips 是它向 Writer /
    Director-B / Options / 绘图写稿 传递设定的唯一通道。放在易变区(user 消息)尾部 —— 不动
    system + history 前缀,故不击穿缓存。空 / 无 tips → 返回空串(不渲染该块)。"""
    items = [str(t).strip() for t in (tips or []) if str(t).strip()]
    if not items:
        return ""
    body = "\n".join(f"- {t}" for t in items)
    return f"【本轮设定提示(来自导演 A,源自设定参考,仅供你这一拍参考)】\n{body}"


def _render_plan(plan: dict[str, Any]) -> str:
    """给 B 的 advisory:A 的意图猜测,明确「仅供参考,以 Writer 成稿为准」。
    只渲染存在的引导/意图字段(不含任何状态);对缺失键宽松容错。"""
    lines = ["【Director-A 预案(仅供参考,以 Writer 成稿为准)】"]
    if plan.get("situation"):
        lines.append(f"情境锚点(situation):{plan['situation']}")
    points = plan.get("beat_points")
    if isinstance(points, list) and points:
        joined = " → ".join(str(p) for p in points)
        lines.append(f"情节要点路标(beat_points,A 拟的推进弧线,以成稿为准):{joined}")
    if plan.get("mood"):
        lines.append(f"情绪基调(mood):{plan['mood']}")
    si, sh = plan.get("scene_intent"), plan.get("scene_hint")
    if si or sh:
        lines.append(f"场景走向(A 的非权威猜测,以成稿为准):intent={si or '未给'};提示={sh or '无'}")
    return "\n".join(lines)


def _task_tail(
    agent_role: AgentRole,
    *,
    writing_brief: str | None,
    narrative: str | None,
    director_a_plan: dict[str, Any] | None,
) -> str:
    if agent_role == "director":
        return f"【任务】\n{DIRECTOR_TASK}"
    if agent_role == "writer":
        if writing_brief is None:
            raise ValueError("writer 角色必须提供 writing_brief")
        return f"【任务】\n{WRITER_TASK}\n\n【本段创作指引(writing_brief)】\n{writing_brief}"
    if agent_role == "director_review":
        if narrative is None:
            raise ValueError("director_review 角色必须提供 narrative(Writer 成稿)")
        parts = [f"【任务】\n{DIRECTOR_REVIEW_TASK}", f"【本轮 Writer 成稿】\n{narrative}"]
        if director_a_plan is not None:
            parts.append(_render_plan(director_a_plan))
        return "\n\n".join(parts)
    if agent_role == "options":
        if narrative is None:
            raise ValueError("options 角色必须提供 narrative(Writer 成稿)")
        # 只读成稿 + 黑板 + tips(易变区尾部),不附 A 预案 —— Options 站在玩家位置看「已写成的当下」。
        return f"【任务】\n{OPTIONS_TASK}\n\n【本轮 Writer 成稿】\n{narrative}"
    raise ValueError(f"未知 agent_role: {agent_role}")


def build_messages(
    agent_role: AgentRole,
    *,
    history: list[Message],
    blackboard: Blackboard,
    user_action: str,
    writing_brief: str | None = None,
    narrative: str | None = None,
    director_a_plan: dict[str, Any] | None = None,
    knowledge: str | None = None,
    style_bible: str | None = None,
    visual_style: str | None = None,
    reference_catalog: str | None = None,
    tips: list[str] | None = None,
    extra_instruction: str | None = None,
) -> list[Message]:
    """构造发送给 DeepSeek 的 messages。

    history 必须是「干净」的历史(user=玩家输入, assistant=叙事),调用方在三个 agent
    全部跑完之后再追加本轮记录,且追加的是干净消息(不含黑板/任务指令)。

    缓存铁律:第一条 system(文风圣经)对所有 agent 逐字节一致;黑板及其后的内容是易变区
    (本就不缓存),因此 illustrator 在易变区追加「画风圣经 + 参考图库清单」不影响前缀命中。
    叙事三 agent 的易变区构造与 M2 逐字节一致,绝不改动。

    知识库(设定圣经库)仅注入 **Director-A**:作为第二条 system 消息插在文风圣经之后、
    history 之前的稳定前缀位置。Writer / Director-B / illustrator 的 messages 与未引入知识库前
    逐字节相同——它们的缓存完全不受影响;A 的知识库基本不变,历史缓存大部分仍命中。
    """
    # 文风圣经:故事自定义优先,否则用全局默认。仍是 system 首条前缀 —— 同一故事内对所有 agent
    # 逐字节一致、跨轮稳定(用户改了才变),缓存铁律不破。
    messages: list[Message] = [{"role": "system", "content": style_bible or STYLE_BIBLE}]
    if agent_role == "director" and knowledge:
        messages.append({"role": "system", "content": _render_knowledge(knowledge)})
    messages.extend(history)

    # tips(导演 A 摘的本轮设定提示)在易变区尾部追加,不动 system+history 前缀 → 不击穿缓存。
    # A 自己是产出方,绝不收 tips(结构上禁掉 director 角色,杜绝误传)。
    tips_block = "" if agent_role == "director" else _render_tips(tips)
    if agent_role == "illustrator":
        # 易变区:黑板 -> 画风圣经 -> 参考图库清单 -> 本轮绘图请求 -> 任务 -> (tips)
        parts = [
            _render_blackboard(blackboard),
            f"【画风圣经】\n{visual_style or ''}",
            f"【参考图库清单】\n{reference_catalog or '(空)'}",
            f"【本轮绘图请求】\n{user_action}",
            f"【任务】\n{ILLUSTRATOR_TASK}",
        ]
    else:
        # 易变区:黑板 -> 玩家输入 -> 任务指令 -> (tips)(前三块顺序/位置同 M1/M2,逐字节不变)
        parts = [
            _render_blackboard(blackboard),
            f"【本轮玩家行动】\n{user_action}",
            _task_tail(
                agent_role,
                writing_brief=writing_brief,
                narrative=narrative,
                director_a_plan=director_a_plan,
            ),
        ]
    if tips_block:
        parts.append(tips_block)
    # 用户对绘图写稿 Agent 的「附加指令」:直接原样接在易变区**最末尾**(tips 之后),不加任何
    # 标签/包装 ——「不加其他东西」。仍在 user 消息内,不动 system+history 前缀 → 不击穿缓存。
    extra = (extra_instruction or "").strip()
    if extra:
        parts.append(extra)
    messages.append({"role": "user", "content": "\n\n".join(parts)})
    return messages
