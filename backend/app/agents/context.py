"""共享上下文构造。

缓存命中的唯一保障:Director 与 Writer 都必须经由 build_messages() 构造 messages,
禁止各自拼装。布局严格如下:

  [system]  仅文风圣经(几乎不变,两 agent 共用,Director 忽略其文学要求)
  [history] 全量对话历史,原生 user/assistant 交替,只追加不修改;
            user = 玩家原始输入,assistant = Writer 写出的叙事(均为「干净」消息,
            不含世界状态/任务指令)
  [user]    最后一条 user 消息,承载易变区,顺序固定:
              1. 世界状态快照(世界设定/角色档案/场景状态)
              2. 本轮玩家实际输入
              3. 该 agent 的任务指令(按 agent_role 区分)

system 与 history 对两个 agent 完全一致;最后一条 user 消息的快照+玩家输入部分也
完全一致,只有尾部任务指令不同。这样 Writer 调用可复用 Director 调用直到尾部之前的
全部前缀缓存。

严禁在 system / history / 快照中插入时间戳、随机 ID 或任何会变动的稳定前缀内容。
"""

import json
from typing import Literal

from app.agents.loader import load_prompt
from app.models.schemas import WorldState, WritingBrief

AgentRole = Literal["director", "writer"]

Message = dict[str, str]

STYLE_BIBLE = load_prompt("style_bible.md")
DIRECTOR_TASK = load_prompt("director_task.md")
WRITER_TASK = load_prompt("writer_task.md")


def _render_snapshot(world_state: WorldState) -> str:
    """渲染易变的世界状态快照。两个 agent 同一回合必须收到逐字节相同的快照,
    因此本函数是纯函数,不引入任何随机/时间内容。"""
    current_scene = world_state.scenes.get(world_state.current_scene_id, {})
    snapshot = {
        "current_scene_id": world_state.current_scene_id,
        "current_scene": current_scene,
        "characters": world_state.characters,
        "story_summary": world_state.story_summary,
    }
    body = json.dumps(snapshot, ensure_ascii=False, indent=2)
    return f"【世界状态快照】\n{body}"


def _render_brief(brief: WritingBrief) -> str:
    body = json.dumps(brief.model_dump(), ensure_ascii=False, indent=2)
    return f"【本段创作指令(writing_brief)】\n{body}"


def _task_tail(agent_role: AgentRole, writing_brief: WritingBrief | None) -> str:
    if agent_role == "director":
        return f"【任务】\n{DIRECTOR_TASK}"
    if agent_role == "writer":
        if writing_brief is None:
            raise ValueError("writer 角色必须提供 writing_brief")
        return f"【任务】\n{WRITER_TASK}\n\n{_render_brief(writing_brief)}"
    raise ValueError(f"未知 agent_role: {agent_role}")


def build_messages(
    agent_role: AgentRole,
    *,
    history: list[Message],
    world_state: WorldState,
    user_action: str,
    writing_brief: WritingBrief | None = None,
) -> list[Message]:
    """构造发送给 DeepSeek 的 messages。

    history 必须是「干净」的历史(user=玩家输入, assistant=叙事),调用方在两个 agent
    都跑完之后再追加本轮记录,且追加的是干净消息(不含快照/任务指令)。
    """
    messages: list[Message] = [{"role": "system", "content": STYLE_BIBLE}]
    messages.extend(history)

    # 易变区:快照 -> 玩家输入 -> 任务指令(顺序固定)
    tail = "\n\n".join(
        [
            _render_snapshot(world_state),
            f"【本轮玩家行动】\n{user_action}",
            _task_tail(agent_role, writing_brief),
        ]
    )
    messages.append({"role": "user", "content": tail})
    return messages
