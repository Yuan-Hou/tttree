"""故事档案 CRUD(纯逻辑)。所有数据按 story_id 隔离;删除时连带清理黑板/Turn/ImageGen/
参考图(含磁盘文件)。"""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blackboard, ImageGen, ReferenceAsset, Story, Turn
from app.storage import BACKEND_ROOT


def empty_blackboard(title: str) -> dict:
    """新故事的初始空黑板:只有标题,场景/角色/物品/notes 皆空,待首回合建立。"""
    return {
        "story_meta": {"title": title, "current_scene": "", "latest_beat": ""},
        "scenes": {},
        "characters": {},
        "items": {},
        "notes": [],
    }


@dataclass
class StoryInfo:
    id: str
    title: str
    created_at: str
    last_active_at: str
    turn_count: int


async def create_story(session: AsyncSession, *, title: str) -> Story:
    story_id = uuid.uuid4().hex
    session.add(Story(id=story_id, title=title))
    session.add(
        Blackboard(story_id=story_id, json_blob=json.dumps(empty_blackboard(title), ensure_ascii=False))
    )
    await session.commit()
    story = await session.get(Story, story_id)
    return story


async def list_stories(session: AsyncSession) -> list[StoryInfo]:
    stories = (await session.execute(select(Story).order_by(Story.last_active_at.desc()))).scalars().all()
    out: list[StoryInfo] = []
    for s in stories:
        n = (
            await session.execute(
                select(func.count()).select_from(Turn).where(Turn.story_id == s.id)
            )
        ).scalar() or 0
        out.append(
            StoryInfo(
                id=s.id,
                title=s.title,
                created_at=s.created_at.isoformat(),
                last_active_at=s.last_active_at.isoformat(),
                turn_count=n,
            )
        )
    return out


async def rename_story(session: AsyncSession, story_id: str, new_title: str) -> Story | None:
    story = await session.get(Story, story_id)
    if story is None:
        return None
    story.title = new_title
    # 标题也同步进黑板 story_meta.title(若有黑板)
    bb_row = await session.get(Blackboard, story_id)
    if bb_row is not None:
        bb = json.loads(bb_row.json_blob)
        bb.setdefault("story_meta", {})["title"] = new_title
        bb_row.json_blob = json.dumps(bb, ensure_ascii=False)
    await session.commit()
    return story


async def touch_story(session: AsyncSession, story_id: str) -> None:
    """更新 last_active_at(每回合推进后调用)。"""
    story = await session.get(Story, story_id)
    if story is not None:
        from app.db.models import _utcnow

        story.last_active_at = _utcnow()
        await session.commit()


async def delete_story(
    session: AsyncSession, story_id: str, *, base_dir: Path = BACKEND_ROOT
) -> bool:
    """删除故事及其全部数据 + 磁盘图片/参考图文件。"""
    story = await session.get(Story, story_id)
    if story is None:
        return False

    # 收集要删的磁盘文件(去重:reuse 的 ImageGen 与原图共享同一文件)
    files: set[str] = set()
    igs = (await session.execute(select(ImageGen).where(ImageGen.story_id == story_id))).scalars().all()
    for ig in igs:
        if ig.output_path:
            files.add(ig.output_path)
    bb_row = await session.get(Blackboard, story_id)
    if bb_row is not None:
        bb = json.loads(bb_row.json_blob)
        for scene in (bb.get("scenes") or {}).values():
            for p in scene.get("image_paths") or []:
                files.add(p)
    refs = (
        await session.execute(select(ReferenceAsset).where(ReferenceAsset.story_id == story_id))
    ).scalars().all()
    for r in refs:
        if r.file_path:
            files.add(r.file_path)

    for rel in files:
        (base_dir / rel).unlink(missing_ok=True)

    # 删除所有表里属于该 story 的行
    await session.execute(delete(ImageGen).where(ImageGen.story_id == story_id))
    await session.execute(delete(Turn).where(Turn.story_id == story_id))
    await session.execute(delete(ReferenceAsset).where(ReferenceAsset.story_id == story_id))
    await session.execute(delete(Blackboard).where(Blackboard.story_id == story_id))
    await session.delete(story)
    await session.commit()
    return True
