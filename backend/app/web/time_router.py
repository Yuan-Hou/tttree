"""时间控制 + 节点上下文(M5-B 的 HTTP 壳)。

纯包壳:底层逻辑全在 M4.5 已验证的 store/turns 函数里(get_step_contexts / rollback_latest_turn /
retry_turn),这里只把它们暴露成 HTTP,不改任何底层逻辑、不碰三段式与缓存。

  - GET  /story/{id}/turn/{n}/contexts  → 某轮三个 agent 各自的完整输入 messages + 输出(M4.5-B)
  - POST /story/{id}/rollback           → 回退最新一轮(M4.5-C-1),可连续回退
  - POST /story/{id}/retry  {entry}     → 最新一轮从 A前/Writer前/B前 重走(M4.5-C-4)

回退/重试的「仅最新轮可用」「进行中禁用」由前端状态机把关;后端只对单次请求做朴素校验。
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blackboard, DrawProposal, ImageGen, Story, Turn
from app.db.session import async_session
from app.turns.draw_proposals import list_proposals
from app.turns.retry import ENTRY_POINTS, stream_retry_turn
from app.turns.rollback import rollback_latest_turn
from app.turns.step_contexts import get_step_contexts, set_step_context
from app.web.deps import get_session
from app.web.jobs import start_turn_job, turn_active
from app.web.sse import sse

from app.web.auth_deps import require_story_owner

router = APIRouter(prefix="/story", tags=["time"], dependencies=[Depends(require_story_owner)])


def _loads(raw: str):
    return json.loads(raw) if raw else None


@router.get("/{story_id}/turn/{turn_index}/contexts")
async def get_turn_contexts(
    story_id: str, turn_index: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """某轮三段式的「显微镜」数据:每个 agent 当时喂进去的完整 messages + 它的输出。"""
    ctx = await get_step_contexts(session, story_id, turn_index)
    if ctx is None:
        raise HTTPException(404, "turn not found")
    turn = (
        await session.execute(
            select(Turn).where(Turn.story_id == story_id, Turn.turn_index == turn_index)
        )
    ).scalar_one()
    return {
        "turn_index": turn_index,
        "user_input": turn.user_input,
        "beat_title": turn.beat_title,
        "director_a": {"messages": ctx["director_a"], "output": _loads(turn.director_a_json)},
        "writer": {"messages": ctx["writer"], "output": turn.narrative},
        # B 的输出 = 落盘后的权威新黑板(reducer 已盖 origin_turn);这是导演真正关心的「这一轮把世界改成了什么」。
        "director_b": {"messages": ctx["director_b"], "output": _loads(turn.blackboard_after)},
        # Options 的输出 = 它给的下一步选项(OptionsOutput JSON);失败/老数据 → None。
        "options": {"messages": ctx["options"], "output": _loads(turn.options_json)},
    }


class EditStepReq(BaseModel):
    messages: list[dict]


@router.put("/{story_id}/turn/{turn_index}/contexts/{step}")
async def put_step_context(
    story_id: str, turn_index: int, step: str, req: EditStepReq,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """改写某轮某步的输入记录(直接改 M4.5-B 存的那份 messages)。仅最新轮可编辑:历史轮不能
    重试,编辑它无意义,且避免误动时间线 —— 要改历史轮,先回退到那一轮。"""
    if step not in ("director_a", "writer", "director_b", "options"):
        raise HTTPException(400, "step 应为 director_a / writer / director_b / options")
    latest = (
        await session.execute(select(func.max(Turn.turn_index)).where(Turn.story_id == story_id))
    ).scalar()
    if latest is None:
        raise HTTPException(404, "story has no turns")
    if turn_index != latest:
        raise HTTPException(409, "只能编辑最新轮的输入记录;要改历史轮,先回退到那一轮")
    ok = await set_step_context(session, story_id, turn_index, step, req.messages)
    if not ok:
        raise HTTPException(404, "turn not found")
    return {"ok": True, "turn_index": turn_index, "step": step, "count": len(req.messages)}


@router.get("/{story_id}/turn/{turn_index}/draws")
async def get_turn_draws(
    story_id: str, turn_index: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """本轮产生的绘图提案(DrawProposal 按 origin_proposal_turn 过滤)—— 与绘图台 GET /proposals
    **同源**,保证「按轮(显微镜)」和「按场景(绘图台)」是同一批数据的两个切面、状态一致。"""
    props = (
        await session.execute(
            select(DrawProposal)
            .where(DrawProposal.story_id == story_id, DrawProposal.origin_proposal_turn == turn_index)
            .order_by(DrawProposal.id)
        )
    ).scalars().all()
    done_ids = [p.done_image_id for p in props if p.done_image_id]
    done_path: dict[int, str] = {}
    if done_ids:
        rows = (await session.execute(select(ImageGen).where(ImageGen.id.in_(done_ids)))).scalars().all()
        done_path = {ig.id: ig.output_path for ig in rows}
    return {
        "turn_index": turn_index,
        "proposals": [
            {
                "id": p.id,
                "scene_slug": p.scene_slug,
                "kind": p.kind,
                "status": p.status,
                "reason": p.reason,
                "origin_proposal_turn": p.origin_proposal_turn,
                "done_image_path": done_path.get(p.done_image_id) if p.done_image_id else None,
            }
            for p in props
        ],
    }


@router.get("/{story_id}/proposals")
async def get_story_proposals(story_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """全故事的绘图待办,按场景聚合所需的数据(绘图台·子步三)。

    返回所有 DrawProposal(跨轮积压)+ 每条 done 的缩略图路径 + 每个场景是否已画 new_scene 基底
    (驱动 variant 待办的门控显示)。绘图台在前端按 scene_slug 分组。
    """
    if await session.get(Story, story_id) is None:
        raise HTTPException(404, "story not found")
    props = await list_proposals(session, story_id)

    done_ids = [p.done_image_id for p in props if p.done_image_id]
    done_path: dict[int, str] = {}
    if done_ids:
        rows = (await session.execute(select(ImageGen).where(ImageGen.id.in_(done_ids)))).scalars().all()
        done_path = {ig.id: ig.output_path for ig in rows}

    # 每个场景已画过哪些 kind 的图(判 variant 门控 / 是否已有基底）
    kind_rows = (
        await session.execute(
            select(ImageGen.scene_slug, ImageGen.kind).where(ImageGen.story_id == story_id)
        )
    ).all()
    scene_kinds: dict[str, set[str]] = {}
    for slug, k in kind_rows:
        scene_kinds.setdefault(slug, set()).add(k)

    bb_row = await session.get(Blackboard, story_id)
    bb = json.loads(bb_row.json_blob) if bb_row and bb_row.json_blob else {}
    scenes_bb = bb.get("scenes") or {}

    # 过往生成结果全列(供绘图台「替代图片」选图,与 RefPicker 来源二一致,不按轮截断)
    past = (
        await session.execute(
            select(ImageGen).where(ImageGen.story_id == story_id, ImageGen.output_path != "").order_by(ImageGen.id)
        )
    ).scalars().all()

    slugs = {p.scene_slug for p in props}
    return {
        "proposals": [
            {
                "id": p.id,
                "scene_slug": p.scene_slug,
                "origin_proposal_turn": p.origin_proposal_turn,
                "kind": p.kind,
                "status": p.status,
                "reason": p.reason,
                "done_image_path": done_path.get(p.done_image_id) if p.done_image_id else None,
            }
            for p in props
        ],
        "scenes": {
            slug: {
                "name": (scenes_bb.get(slug) or {}).get("name", slug),
                "has_new_scene": "new_scene" in scene_kinds.get(slug, set()),
                "has_variant": "variant" in scene_kinds.get(slug, set()),
                "exists": slug in scenes_bb,
            }
            for slug in slugs
        },
        "past_images": [
            {"imagegen_id": ig.id, "scene_slug": ig.scene_slug, "kind": ig.kind, "output_path": ig.output_path}
            for ig in past
        ],
    }


@router.post("/{story_id}/rollback")
async def post_rollback(story_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    if await session.get(Story, story_id) is None:
        raise HTTPException(404, "story not found")
    r = await rollback_latest_turn(session, story_id)
    if not r.ok:
        raise HTTPException(400, r.reason or "回退失败")
    return {
        "ok": True,
        "rolled_back_turn": r.rolled_back_turn,
        "new_latest_turn": r.new_latest_turn,
        "released_scene_slugs": r.released_scene_slugs,
        "released_image_paths": r.released_image_paths,
        "blackboard": r.blackboard,
    }


class RetryReq(BaseModel):
    entry: str  # director_a | writer | director_b | options


@router.post("/{story_id}/retry")
async def post_retry(story_id: str, req: RetryReq) -> StreamingResponse:
    """最新一轮从切入点重走 —— 临时 SSE 流(逐 token 看各重走 agent 的输出)。

    与 POST /turn 同构:开自己的 async_session;并复用同一把回合并发闸(submit/retry 互斥)+ 后台作业
    存活(刷新/关页不取消 → 跑到底落盘)。事件见 stream_retry_turn;失败前 DB 不变 → 原轮完好。
    """
    if req.entry not in ENTRY_POINTS:
        raise HTTPException(400, f"entry 应为 {ENTRY_POINTS} 之一")
    if turn_active(story_id):
        raise HTTPException(409, "本故事已有进行中的回合,请稍候")

    async def _events() -> AsyncIterator[str]:
        async with async_session() as s:
            if await s.get(Story, story_id) is None:
                yield sse({"type": "error", "reason": "story not found"})
                return
            async for ev in stream_retry_turn(s, story_id, req.entry):
                yield sse(ev)

    return start_turn_job(story_id, _events(), meta={"kind": "retry", "entry": req.entry})
