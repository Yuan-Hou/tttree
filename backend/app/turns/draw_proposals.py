"""绘图待办(DrawProposal)的落库/清理/查询(M5-B 绘图语义升级·子步一)。

核心规则(钉死):绘图 kind 按**场景诞生点**判定,不按发起轮——
  提案产生轮 == 场景 origin_turn → new_scene;> origin_turn → variant。
origin_turn 是后端权威(reducer 打点),故 kind 也在后端定,不信任 Director-B 的回显。

提案与轮次绑定:某轮的 B 产出的提案 origin_proposal_turn = 该轮;回退/重试作废某轮 →
该轮的提案随之删除(reducer 重跑会重新落库),与 M4.5-C 的轮次作废逻辑一致。
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DrawProposal


def kind_for(blackboard: dict, scene_slug: str, proposal_turn: int) -> str | None:
    """按场景 origin_turn 判 kind。场景不在黑板(无诞生点)→ None(跳过,不落这条提案)。"""
    scene = (blackboard.get("scenes") or {}).get(scene_slug)
    if not isinstance(scene, dict):
        return None
    origin = scene.get("origin_turn")
    if origin is None:
        return None
    return "new_scene" if origin == proposal_turn else "variant"


def persist_draw_proposals(
    session: AsyncSession,
    *,
    story_id: str,
    turn_index: int,
    proposals: list[dict],
    blackboard: dict,
) -> list[DrawProposal]:
    """把本轮 B 的 draw_proposals 落库成 pending 待办(kind 按 origin_turn 权威判定)。
    只 add 到 session、不 commit(由调用方 reduce_turn 的统一 commit 一起落)。"""
    rows: list[DrawProposal] = []
    for p in proposals:
        if not isinstance(p, dict):
            continue
        slug = p.get("scene_slug")
        if not slug:
            continue
        kind = kind_for(blackboard, slug, turn_index)
        if kind is None:
            continue  # 提案指向的场景不在黑板/无诞生点 → 跳过
        row = DrawProposal(
            story_id=story_id,
            scene_slug=slug,
            origin_proposal_turn=turn_index,
            kind=kind,
            status="pending",
            reason=p.get("reason") or "",
        )
        session.add(row)
        rows.append(row)
    return rows


async def delete_draw_proposals_for_turn(
    session: AsyncSession, story_id: str, turn_index: int
) -> None:
    """作废某轮 → 删该轮产生的提案(依附的轮没了)。不 commit(由 rollback 的统一 commit 落)。
    图资产(ImageGen)与已画图本身不动——这里删的只是「待办/提案」这层轮次元数据。"""
    await session.execute(
        delete(DrawProposal).where(
            DrawProposal.story_id == story_id, DrawProposal.origin_proposal_turn == turn_index
        )
    )


async def get_proposal(session: AsyncSession, story_id: str, proposal_id: int) -> DrawProposal | None:
    p = await session.get(DrawProposal, proposal_id)
    return p if p is not None and p.story_id == story_id else None


async def mark_proposal_done(session: AsyncSession, proposal_id: int, image_id: int | None) -> None:
    """画完该提案 → status=done、done_image_id 指向生成的 ImageGen。"""
    p = await session.get(DrawProposal, proposal_id)
    if p is not None:
        p.status = "done"
        p.done_image_id = image_id
        await session.commit()


async def list_proposals(session: AsyncSession, story_id: str) -> list[DrawProposal]:
    return list(
        (
            await session.execute(
                select(DrawProposal)
                .where(DrawProposal.story_id == story_id)
                .order_by(DrawProposal.origin_proposal_turn, DrawProposal.id)
            )
        ).scalars().all()
    )
