"""故事内设置的读写(子步一:模型设置)。每故事一行,get-or-create;随 fork / delete 连带。

resolve_agent_model 是接入层与 orchestration 之间的唯一解析点:
按「该 agent 的覆盖 → 否则全局默认」给出最终模型 id,再交给 registry.resolve_chat 取 client。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StorySettings
from app.llm.registry import AGENT_KEYS, DEFAULT_MODEL_ID, is_known_model


async def get_or_create_settings(session: AsyncSession, story_id: str) -> StorySettings:
    """取该故事设置;无则按默认(全局 = deepseek,各 agent 不覆盖)新建一行并提交。"""
    row = await session.get(StorySettings, story_id)
    if row is None:
        row = StorySettings(story_id=story_id, default_model=DEFAULT_MODEL_ID)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def resolve_agent_model(s: StorySettings, agent: str) -> str:
    """该 agent 实际使用的模型 id:有覆盖用覆盖,否则用全局默认。"""
    if agent not in AGENT_KEYS:
        raise ValueError(f"未知 agent: {agent!r}(应为 {AGENT_KEYS})")
    override = (getattr(s, f"{agent}_model") or "").strip()
    return override or s.default_model


def settings_to_dict(s: StorySettings) -> dict:
    return {
        "default_model": s.default_model,
        "overrides": {k: getattr(s, f"{k}_model") for k in AGENT_KEYS},
        "effective": {k: resolve_agent_model(s, k) for k in AGENT_KEYS},
    }


async def update_settings(
    session: AsyncSession,
    story_id: str,
    *,
    default_model: str | None = None,
    overrides: dict[str, str] | None = None,
) -> StorySettings:
    """更新模型设置。default_model 必须是已知模型;overrides 的值可为 ""(=回到全局默认),
    非空则必须是已知模型。只改传入的字段,其余保持不变。"""
    row = await get_or_create_settings(session, story_id)

    if default_model is not None:
        if not is_known_model(default_model):
            raise ValueError(f"未知模型: {default_model!r}")
        row.default_model = default_model

    if overrides is not None:
        for agent, model_id in overrides.items():
            if agent not in AGENT_KEYS:
                raise ValueError(f"未知 agent: {agent!r}")
            mid = (model_id or "").strip()
            if mid and not is_known_model(mid):
                raise ValueError(f"未知模型: {mid!r}")
            setattr(row, f"{agent}_model", mid)

    await session.commit()
    await session.refresh(row)
    return row
