"""故事档案 CRUD(纯逻辑)。所有数据按 story_id 隔离;删除时连带清理黑板/Turn/ImageGen/
参考图(含磁盘文件)。"""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blackboard, ImageGen, Knowledge, ReferenceAsset, Story, Turn
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


async def fork_story(session: AsyncSession, story_id: str) -> Story | None:
    """副本(fork = 检查点 = 后悔药):把整个故事档案克隆成一个全新的独立 story。

    复制:黑板 + 所有 Turn(含 M4.5-B 的每步上下文)+ 所有 ImageGen 记录 + 参考图库 +
    知识库。新 story 分配新 story_id、title 带「(副本)」后缀。**图片文件物理共享**——副本的
    ImageGen/黑板 image_paths/参考图 file_path 都指向同一批磁盘文件,不复制文件(图是花钱
    生成的真实资产)。共享文件模型要求 delete_story 删文件前先查引用,见 M4.5-C-3。

    用户在回退/重试等破坏性操作前手动建副本作后悔药。返回新 Story;源不存在返回 None。
    """
    src = await session.get(Story, story_id)
    if src is None:
        return None
    new_id = uuid.uuid4().hex
    new_story = Story(id=new_id, title=f"{src.title}(副本)")
    session.add(new_story)

    # 黑板(整存复制;image_paths 里的磁盘路径原样指向共享文件)
    bb = await session.get(Blackboard, story_id)
    if bb is not None:
        session.add(Blackboard(story_id=new_id, json_blob=bb.json_blob))

    # 知识库
    kb = await session.get(Knowledge, story_id)
    if kb is not None:
        session.add(Knowledge(story_id=new_id, content=kb.content))

    # Turn(逐轮复制,含每步完整上下文 + 保留原逐轮时间戳)
    turns = (
        await session.execute(select(Turn).where(Turn.story_id == story_id).order_by(Turn.turn_index))
    ).scalars().all()
    for t in turns:
        session.add(Turn(
            story_id=new_id, turn_index=t.turn_index, beat_title=t.beat_title, user_input=t.user_input,
            narrative=t.narrative, director_a_json=t.director_a_json, director_b_json=t.director_b_json,
            blackboard_after=t.blackboard_after, director_a_messages=t.director_a_messages,
            writer_messages=t.writer_messages, director_b_messages=t.director_b_messages, created_at=t.created_at,
        ))

    # 参考图库(file_path 共享同一磁盘文件)。复制会拿到新 id,记下 旧id→新id 映射以重写 ImageGen 引用。
    ref_id_map: dict[int, int] = {}
    refs = (
        await session.execute(select(ReferenceAsset).where(ReferenceAsset.story_id == story_id).order_by(ReferenceAsset.id))
    ).scalars().all()
    for r in refs:
        nr = ReferenceAsset(story_id=new_id, label=r.label, description=r.description,
                            category=r.category, file_path=r.file_path)
        session.add(nr)
        await session.flush()  # 取到新 id
        ref_id_map[r.id] = nr.id

    # ImageGen(output_path/ref_image_paths 共享磁盘文件;ref_asset_ids 重映射到副本自己的参考图 id)
    igs = (
        await session.execute(select(ImageGen).where(ImageGen.story_id == story_id).order_by(ImageGen.id))
    ).scalars().all()
    for ig in igs:
        old_ref_ids = json.loads(ig.ref_asset_ids or "[]")
        new_ref_ids = [ref_id_map.get(x, x) for x in old_ref_ids]
        session.add(ImageGen(
            story_id=new_id, scene_slug=ig.scene_slug, kind=ig.kind, final_prompt=ig.final_prompt,
            ref_asset_ids=json.dumps(new_ref_ids, ensure_ascii=False), ref_image_paths=ig.ref_image_paths,
            output_path=ig.output_path, origin=ig.origin, source_turn=ig.source_turn,
        ))

    await session.commit()
    return await session.get(Story, new_id)


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


async def _files_referenced_by_other_stories(session: AsyncSession, story_id: str) -> set[str]:
    """收集**除本 story 外**所有 story 仍在引用的磁盘文件(相对路径)。

    自 M4.5-C-2 副本与原档物理共享图片文件起,删某 story 前必须查清:该文件是否还被别的
    story 引用。引用来源穷举:ImageGen.output_path / ImageGen.ref_image_paths(历史图引用)/
    ReferenceAsset.file_path / 各 story 黑板 scenes.*.image_paths。
    """
    refs: set[str] = set()
    igs = (await session.execute(select(ImageGen).where(ImageGen.story_id != story_id))).scalars().all()
    for ig in igs:
        if ig.output_path:
            refs.add(ig.output_path)
        for p in json.loads(ig.ref_image_paths or "[]"):
            refs.add(p)
    assets = (await session.execute(select(ReferenceAsset).where(ReferenceAsset.story_id != story_id))).scalars().all()
    for a in assets:
        if a.file_path:
            refs.add(a.file_path)
    bbs = (await session.execute(select(Blackboard).where(Blackboard.story_id != story_id))).scalars().all()
    for row in bbs:
        for scene in (json.loads(row.json_blob).get("scenes") or {}).values():
            for p in scene.get("image_paths") or []:
                refs.add(p)
    return refs


async def delete_story(
    session: AsyncSession, story_id: str, *, base_dir: Path = BACKEND_ROOT
) -> bool:
    """删除故事及其全部数据 + 磁盘图片/参考图文件。

    共享文件模型(M4.5-C-2 副本)下的关键约束:物理删某文件前,先查它是否还被**其他 story**
    引用——仅当无任何其他 story 引用时才物理删,否则只删本 story 的数据库记录、保留文件。
    """
    story = await session.get(Story, story_id)
    if story is None:
        return False

    # 收集本 story 引用的磁盘文件(去重:reuse 的 ImageGen 与原图共享同一文件)
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

    # 仅物理删「无其他 story 再引用」的文件;仍被引用的(如副本共享)保留文件,只删本 story 库记录。
    still_referenced = await _files_referenced_by_other_stories(session, story_id)
    for rel in files:
        if rel not in still_referenced:
            (base_dir / rel).unlink(missing_ok=True)

    # 删除所有表里属于该 story 的行
    await session.execute(delete(ImageGen).where(ImageGen.story_id == story_id))
    await session.execute(delete(Turn).where(Turn.story_id == story_id))
    await session.execute(delete(ReferenceAsset).where(ReferenceAsset.story_id == story_id))
    await session.execute(delete(Blackboard).where(Blackboard.story_id == story_id))
    await session.execute(delete(Knowledge).where(Knowledge.story_id == story_id))
    await session.delete(story)
    await session.commit()
    return True
