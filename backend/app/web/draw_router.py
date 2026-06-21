"""绘图的 Web 人在回路(M4 的「图片线」:独立于文本线、异步、用户驱动)。

两步,对应 M3 验证过的三阶段,绘图逻辑不重写、只把「等 CLI input()」换成「等 HTTP 请求」:

  ① POST /story/{id}/draw          → prepare_draft 写稿(不出图、不花钱),返回 draft_ready,
                                      并把这份**已审阅**的稿暂存(draft_id),供 confirm 取用。
  ② POST /story/{id}/draw/confirm  → 带用户编辑后的稿 + 决策:
       - confirm → 返回一条**短命 SSE 流**:立即推 image_generating,后台异步 execute_image
                   (gpt-image-2,花钱),完成推 image_ready / image_failed,关流。
       - reuse   → 关联已有图,不调 API(同步 JSON 返回)。
       - skip    → 不出图(同步 JSON 返回)。

确认闸门无旁路:execute_image 的**唯一**触达路径是 confirm 决策,且作用于用户先在 /draw 审阅、
再在 confirm body 里编辑过的同一份稿。没有任何端点能跳过 confirm 直接出图。

晚到事件按 M4-B 的模型处理:不挂长连接通知流——confirm 自己开一条临时流,推完即关。
"""

import json
import tempfile
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assets.reference_store import list_references
from app.db.models import DrawProposal, ImageGen, Story, Turn
from app.db.session import async_session
from app.imaging.draw_service import (
    DraftBundle,
    apply_decision,
    picture_from_refs,
    prepare_draft,
    substitute_picture,
    write_illustration_draft,
)
from app.imaging.executor import ImageGenError, ResolvedRefs, resolve_references
from app.models.schemas import ReferenceRef
from app.storage import BACKEND_ROOT
from app.stories.store import touch_story
from app.turns.draw_proposals import get_proposal, kind_for, mark_proposal_done
from app.web.auth_deps import require_story_owner
from app.web.jobs import start_draw_job
from app.web.sse import sse

router = APIRouter(prefix="/story", tags=["draw"], dependencies=[Depends(require_story_owner)])


@dataclass
class _PendingDraft:
    """/draw 与 /draw/confirm 之间暂存的、用户已审阅的稿。confirm 作用于这同一份,
    保证「确认的就是审阅过的那张」——这是确认闸门成立的前提。"""

    bundle: DraftBundle
    story_id: str
    origin: str
    source_turn: int | None
    proposal_id: int | None = None


# 进程内暂存:draft_id → 已审阅的稿。一次性,确认/复用/跳过后即弹出。
_PENDING: dict[str, _PendingDraft] = {}


class DrawReq(BaseModel):
    # 提案制(M5-B 绘图语义升级):优先按 proposal_id 画 —— kind 与截断轮都从提案取(后端权威)。
    proposal_id: int | None = None
    # 临时/主动制:直接指定场景;截断/归属轮 = source_turn(默认最新轮),kind 按 origin_turn 判。
    scene: str | None = None
    source: str = "user_initiated"  # user_initiated | director_b_proposal(写入 ImageGen.origin)
    request: str | None = None  # 绘图请求文本(默认据场景生成)
    source_turn: int | None = None  # 截断到哪一轮的对话+黑板;proposal 时由其 origin 轮覆盖
    # 用户对绘图写稿 Agent 的「附加指令」:原样接到其输入末尾(不加包装),出提示词前的临时叮嘱。
    extra_instruction: str | None = None


async def _history_through_turn(s: AsyncSession, story_id: str, n: int) -> list[dict]:
    """该绘图提案所属轮 N 的上下文截断:只取 turn_index ≤ N 的干净对话(user=输入, assistant=叙事)。
    不含未来轮 —— 否则会把后续剧情画进早期场景图,时间错乱。"""
    turns = (
        await s.execute(
            select(Turn).where(Turn.story_id == story_id, Turn.turn_index <= n).order_by(Turn.turn_index)
        )
    ).scalars().all()
    history: list[dict] = []
    for t in turns:
        history.append({"role": "user", "content": t.user_input})
        history.append({"role": "assistant", "content": t.narrative})
    return history


async def _blackboard_after_turn(s: AsyncSession, story_id: str, n: int) -> dict | None:
    """第 N 轮结束时刻的黑板(Turn.blackboard_after)。绘图按这一份截断,不看未来轮。"""
    t = (
        await s.execute(select(Turn).where(Turn.story_id == story_id, Turn.turn_index == n))
    ).scalar_one_or_none()
    return json.loads(t.blackboard_after) if t and t.blackboard_after else None


async def _tips_for_turn(s: AsyncSession, story_id: str, n: int | None) -> list[str]:
    """取第 N 轮导演 A 的设定提示(tips)。绘图归属到第 N 轮 → 用那一轮 A 摘的设定递给绘图写稿。
    无归属轮 / 取不到 / 老数据无该字段 → 空列表(优雅降级,不影响出图)。"""
    if n is None:
        return []
    t = (
        await s.execute(select(Turn).where(Turn.story_id == story_id, Turn.turn_index == n))
    ).scalar_one_or_none()
    if t is None or not t.director_a_json:
        return []
    try:
        tips = json.loads(t.director_a_json).get("tips")
    except (json.JSONDecodeError, AttributeError):
        return []
    return [str(x) for x in tips] if isinstance(tips, list) else []


async def _scene_image_kinds(s: AsyncSession, story_id: str, scene_slug: str) -> set[str]:
    rows = (
        await s.execute(
            select(ImageGen.kind).where(ImageGen.story_id == story_id, ImageGen.scene_slug == scene_slug)
        )
    ).scalars().all()
    return set(rows)


class ConfirmReq(BaseModel):
    draft_id: str = Field(min_length=1)
    decision: str  # confirm(出图,花钱) | reuse(复用已有图) | skip(跳过)
    prompt: str | None = None  # 用户编辑后的提示词;省略则用原稿
    reuse_image_path: str | None = None  # decision=reuse 时可指定复用哪张历史图
    # 用户编辑后的引用清单(可删 Agent 的某条、加库里另一张)。省略则用 Agent 原始清单。
    # 每条:{semantic_name, source(reference_asset/history_image), asset_id?, image_path?, purpose}
    references: list[dict] | None = None


def _refs_payload(bundle: DraftBundle, assets_by_id: dict[int, object]) -> list[dict]:
    """引用清单 → 语义名 + 用途 + 缩略图路径(全程语义名,执行层才映射文件)。"""
    out: list[dict] = []
    for r in bundle.draft.reference_manifest:
        if r.source == "reference_asset":
            asset = assets_by_id.get(r.asset_id)
            preview = getattr(asset, "file_path", None) if asset else None
        else:
            preview = r.image_path
        out.append(
            {
                "semantic_name": r.semantic_name,
                "source": r.source,
                "purpose": r.purpose,
                "asset_id": r.asset_id,
                "image_path": r.image_path,
                "preview_path": preview,  # 相对 backend 根,供前端取缩略图
            }
        )
    return out


@router.post("/{story_id}/draw")
async def post_draw(story_id: str, req: DrawReq) -> dict:
    """阶段①:绘图 Agent 写稿(不出图、不花钱)。绘图不限最新轮:任意轮的提案都可画/重画。

    kind 按场景诞生点权威判定(proposal 直接取其 kind;临时制按 origin_turn vs 截断轮算);
    上下文按该提案所属轮 N 截断;variant 需场景已有 new_scene 基底(否则 409);重绘 new_scene
    且已有 variant 时回 warn_redraw_base=true(前端弹警告,用户确认后再 confirm)。
    """
    async with async_session() as s:
        if await s.get(Story, story_id) is None:
            raise HTTPException(404, "story not found")
        latest = (
            await s.execute(select(func.max(Turn.turn_index)).where(Turn.story_id == story_id))
        ).scalar()

        # ── 解析:提案制 优先于 临时制 ──
        proposal_id = req.proposal_id
        origin = req.source
        if proposal_id is not None:
            prop = await get_proposal(s, story_id, proposal_id)
            if prop is None:
                raise HTTPException(404, "draw proposal not found")
            scene_slug, kind, n = prop.scene_slug, prop.kind, prop.origin_proposal_turn
            origin = "director_b_proposal"
        else:
            if not req.scene:
                raise HTTPException(400, "需提供 proposal_id 或 scene")
            scene_slug = req.scene
            n = req.source_turn if req.source_turn is not None else latest
            if n is None:
                raise HTTPException(400, "故事尚无回合,无法绘图")

        # ── 该轮的黑板(截断)+ 场景校验 ──
        bb_n = await _blackboard_after_turn(s, story_id, n)
        if bb_n is None:
            raise HTTPException(404, f"turn {n} not found")
        if scene_slug not in (bb_n.get("scenes") or {}):
            raise HTTPException(400, f"scene {scene_slug!r} not in turn {n} blackboard")
        if proposal_id is None:
            kind = kind_for(bb_n, scene_slug, n)
            if kind is None:
                raise HTTPException(400, f"scene {scene_slug!r} has no origin_turn")

        # ── variant 基底门控 + 重绘 new_scene 警告 ──
        kinds = await _scene_image_kinds(s, story_id, scene_slug)
        if kind == "variant" and "new_scene" not in kinds:
            raise HTTPException(409, "variant 需先绘制该场景的 new_scene 基底图")
        warn_redraw_base = kind == "new_scene" and "variant" in kinds

        # ── 写稿(上下文截断到第 N 轮)──
        history = await _history_through_turn(s, story_id, n)
        request = req.request or f"为场景 {scene_slug} 画一张图,定格第 {n} 轮的画面。"
        tips = await _tips_for_turn(s, story_id, n)  # 第 N 轮 A 的设定提示 → 递给绘图写稿
        bundle = await prepare_draft(
            s, story_id=story_id, blackboard=bb_n, scene_slug=scene_slug,
            draw_request=request, history=history, kind=kind, tips=tips,
            extra_instruction=req.extra_instruction,
        )
        assets = await list_references(s, story_id)
        # 用户手动选参考图的「过往绘制结果」列表:整故事所有 ImageGen(含手动草稿),不按轮截断、不过滤。
        # 与喂 Agent 的候选池(prepare_draft 内 build_history_catalog,排手动图)是两条独立查询。
        past = (
            await s.execute(
                select(ImageGen).where(ImageGen.story_id == story_id, ImageGen.output_path != "").order_by(ImageGen.id)
            )
        ).scalars().all()

    draft_id = uuid.uuid4().hex
    _PENDING[draft_id] = _PendingDraft(
        bundle=bundle, story_id=story_id, origin=origin, source_turn=n, proposal_id=proposal_id
    )
    return {
        "type": "draft_ready",
        "draft_id": draft_id,
        "scene": bundle.scene_slug,
        "kind": bundle.draft.kind,  # 后端权威 kind
        "draw_turn": n,
        "proposal_id": proposal_id,
        "warn_redraw_base": warn_redraw_base,
        "prompt_text": bundle.draft.prompt_text,  # 可编辑;confirm 时回传
        "refs": _refs_payload(bundle, {a.id: a for a in assets}),
        "history": [{"semantic_name": h["semantic_name"], "image_path": h["image_path"]} for h in bundle.history],
        "library": [
            {"asset_id": a.id, "label": a.label, "description": a.description,
             "category": a.category, "file_path": a.file_path}
            for a in assets
        ],
        # 自由选择参考图的第二来源(过往绘制结果,全列):供手动绘图稿的 RefPicker。
        "past_images": [{"imagegen_id": ig.id, "scene_slug": ig.scene_slug, "kind": ig.kind,
                         "output_path": ig.output_path} for ig in past],
    }


async def _confirm_events(
    pending: _PendingDraft, final_prompt: str, request_id: str, resolved: ResolvedRefs | None
) -> AsyncIterator[str]:
    """confirm 的短命 SSE 流:先推 generating(此时 execute_image 尚未开始/在跑,请求不阻塞),
    await 真出图(真实异步间隔),再推 ready / failed,关流。resolved=用户编辑后的引用清单。"""
    yield sse({"type": "image_generating", "scene": pending.bundle.scene_slug, "request_id": request_id})
    try:
        async with async_session() as s:
            res = await apply_decision(
                s,
                decision="confirm",
                bundle=pending.bundle,
                final_prompt=final_prompt,
                story_id=pending.story_id,
                origin=pending.origin,
                source_turn=pending.source_turn,
                resolved=resolved,
            )
            await touch_story(s, pending.story_id)
            if pending.proposal_id is not None:  # 画完该提案 → status=done、指向生成的 ImageGen
                await mark_proposal_done(s, pending.proposal_id, res.get("imagegen_id"))
    except ImageGenError as exc:  # API 拒绝/网络等:已捕获,不崩溃
        yield sse(
            {"type": "image_failed", "scene": pending.bundle.scene_slug, "reason": str(exc), "request_id": request_id}
        )
        return
    yield sse(
        {
            "type": "image_ready",
            "scene": res["scene"],
            "image_path": res["output_path"],
            "api_call": res["api_call"],
            "request_id": request_id,
        }
    )


@router.post("/{story_id}/draw/confirm")
async def post_confirm(story_id: str, req: ConfirmReq):
    """阶段③:据用户决策出图/复用/跳过。confirm 是唯一通往真实出图(花钱)的路径。"""
    pending = _PENDING.pop(req.draft_id, None)
    if pending is None or pending.story_id != story_id:
        raise HTTPException(404, "draft not found (expired or wrong story)")

    final_prompt = req.prompt if req.prompt is not None else pending.bundle.draft.prompt_text

    # 用户编辑过引用清单 → 据此重建 resolved(执行层按用户最终清单传图、ImageGen 也记这一份)
    resolved: ResolvedRefs | None = None
    if req.references is not None:
        manifest = [ReferenceRef.model_validate(r) for r in req.references]
        async with async_session() as s:
            assets = await list_references(s, story_id)
        resolved = resolve_references(manifest, {a.id: a for a in assets})

    if req.decision == "confirm":
        request_id = uuid.uuid4().hex
        # 出图作为后台绘图作业:刷新/关页不取消 → gpt-image-2 出的图照常落盘(不浪费已花的钱)。
        return start_draw_job(
            story_id,
            f"confirm:{req.draft_id}",
            _confirm_events(pending, final_prompt, request_id, resolved),
            meta={"kind": "confirm", "scene": pending.bundle.scene_slug},
        )

    if req.decision in {"reuse", "skip"}:  # 不花钱,同步返回
        async with async_session() as s:
            res = await apply_decision(
                s,
                decision=req.decision,
                bundle=pending.bundle,
                final_prompt=final_prompt,
                story_id=story_id,
                origin=pending.origin,
                source_turn=pending.source_turn,
                reuse_image_path=req.reuse_image_path,
                resolved=resolved,
            )
            await touch_story(s, story_id)
            # 复用已有图也算把该提案画完(关联了图);跳过则不动提案,留它继续 pending。
            if req.decision == "reuse" and pending.proposal_id is not None:
                await mark_proposal_done(s, pending.proposal_id, res.get("imagegen_id"))
        return res

    raise HTTPException(400, f"unknown decision: {req.decision!r}")


# ─────────────────────────────────────────────────────────────
#  提案制绘图:写稿节点(DeepSeek)/ 画图节点(gpt-image-2)真正分开,各自输入/输出/重试。
#  写稿步持久化在 DrawProposal(draft_messages/draft_prompt/draft_manifest);画图步据用户最终
#  确认的提示词 + 自由选择的参考图出图。两步解绑:写稿重试=重写词,画图重试=只重画。
# ─────────────────────────────────────────────────────────────


def _manifest_payload(manifest: list[ReferenceRef], assets_by_id: dict[int, object]) -> list[dict]:
    out: list[dict] = []
    for r in manifest:
        preview = getattr(assets_by_id.get(r.asset_id), "file_path", None) if r.source == "reference_asset" else r.image_path
        out.append({
            "semantic_name": r.semantic_name, "source": r.source, "purpose": r.purpose,
            "asset_id": r.asset_id, "image_path": r.image_path, "preview_path": preview,
        })
    return out


async def _resolve_proposal(s: AsyncSession, story_id: str, proposal_id: int):
    """取提案 → (prop, N, 第N轮黑板, variant门控, 重绘警告)。供写稿/画图共用。"""
    prop = await get_proposal(s, story_id, proposal_id)
    if prop is None:
        raise HTTPException(404, "draw proposal not found")
    n = prop.origin_proposal_turn
    bb_n = await _blackboard_after_turn(s, story_id, n)
    if bb_n is None:
        raise HTTPException(404, f"turn {n} not found")
    if prop.scene_slug not in (bb_n.get("scenes") or {}):
        raise HTTPException(400, f"scene {prop.scene_slug!r} not in turn {n} blackboard")
    kinds = await _scene_image_kinds(s, story_id, prop.scene_slug)
    variant_gated = prop.kind == "variant" and "new_scene" not in kinds
    warn = prop.kind == "new_scene" and "variant" in kinds
    return prop, n, bb_n, variant_gated, warn


@router.get("/{story_id}/draw/proposal/{proposal_id}")
async def get_proposal_draw(story_id: str, proposal_id: int) -> dict:
    """画图/写稿节点的展示数据:写稿输入(可编辑)+ 写稿输出(提示词稿)+ 参考图两类来源 + 已出的图。"""
    async with async_session() as s:
        prop, n, _bb, variant_gated, warn = await _resolve_proposal(s, story_id, proposal_id)
        draft_messages = json.loads(prop.draft_messages or "[]")
        draft_prompt = prop.draft_prompt
        manifest = [ReferenceRef.model_validate(r) for r in json.loads(prop.draft_manifest or "[]")]
        scene_slug, kind, status = prop.scene_slug, prop.kind, prop.status
        done_path = None
        if prop.done_image_id:
            ig = await s.get(ImageGen, prop.done_image_id)
            done_path = ig.output_path if ig else None
        assets = await list_references(s, story_id)
        past = (
            await s.execute(
                select(ImageGen).where(ImageGen.story_id == story_id, ImageGen.output_path != "").order_by(ImageGen.id)
            )
        ).scalars().all()
    return {
        "proposal_id": proposal_id, "scene_slug": scene_slug, "kind": kind, "status": status,
        "origin_proposal_turn": n, "done_image_path": done_path,
        "draft_messages": draft_messages, "draft_prompt": draft_prompt,
        "draft_manifest": _manifest_payload(manifest, {a.id: a for a in assets}),
        "variant_gated": variant_gated, "warn_redraw_base": warn,
        "library": [{"asset_id": a.id, "label": a.label, "description": a.description,
                     "category": a.category, "file_path": a.file_path} for a in assets],
        # 过往绘制结果:整故事所有 ImageGen 的 output(不按轮截断,纯视觉素材任选)
        "past_images": [{"imagegen_id": ig.id, "scene_slug": ig.scene_slug, "kind": ig.kind,
                         "output_path": ig.output_path} for ig in past],
    }


class WriteReq(BaseModel):
    messages: list[dict] | None = None  # 编辑过的写稿输入;省略=按截断上下文新建
    request: str | None = None
    # 用户「附加指令」:仅在新建 messages 时原样接到写稿 Agent 输入末尾(不加包装)。
    extra_instruction: str | None = None


@router.post("/{story_id}/draw/proposal/{proposal_id}/write")
async def post_write(story_id: str, proposal_id: int, req: WriteReq) -> dict:
    """写稿节点(重)跑:绘图 Agent 写提示词。输出是文字稿,绝不是图。"""
    async with async_session() as s:
        prop, n, bb_n, _gated, warn = await _resolve_proposal(s, story_id, proposal_id)
        scene_slug, kind = prop.scene_slug, prop.kind
        history = await _history_through_turn(s, story_id, n)
        request = req.request or f"为场景 {scene_slug} 画一张图,定格第 {n} 轮的画面。"
        tips = await _tips_for_turn(s, story_id, n)  # 第 N 轮 A 的设定提示(仅新建时注入;复用 messages 已含)
        used, draft = await write_illustration_draft(
            s, story_id=story_id, blackboard=bb_n, scene_slug=scene_slug,
            draw_request=request, history=history, kind=kind, messages=req.messages, tips=tips,
            extra_instruction=req.extra_instruction,
        )
        manifest_dicts = [r.model_dump() for r in draft.reference_manifest]
        prop.draft_messages = json.dumps(used, ensure_ascii=False)
        prop.draft_prompt = draft.prompt_text
        prop.draft_manifest = json.dumps(manifest_dicts, ensure_ascii=False)
        await s.commit()
        assets = await list_references(s, story_id)
    return {
        "proposal_id": proposal_id, "kind": kind, "warn_redraw_base": warn,
        "draft_messages": used, "draft_prompt": draft.prompt_text,
        "draft_manifest": _manifest_payload(draft.reference_manifest, {a.id: a for a in assets}),
    }


class DraftMsgsReq(BaseModel):
    messages: list[dict]


@router.put("/{story_id}/draw/proposal/{proposal_id}/draft-messages")
async def put_draft_messages(story_id: str, proposal_id: int, req: DraftMsgsReq) -> dict:
    """编辑写稿节点的输入记录(像三段式节点那样)。之后「重写提示词」会用这份。"""
    async with async_session() as s:
        prop = await get_proposal(s, story_id, proposal_id)
        if prop is None:
            raise HTTPException(404, "draw proposal not found")
        prop.draft_messages = json.dumps(req.messages, ensure_ascii=False)
        await s.commit()
    return {"ok": True, "count": len(req.messages)}


class PictureReq(BaseModel):
    prompt: str = Field(min_length=1)
    # 用户最终确认的参考图集合(两类来源)。每条:{source, asset_id?, image_path?, semantic_name?, purpose?}
    references: list[dict] = Field(default_factory=list)


async def _picture_events(story_id: str, proposal_id: int, prompt: str, references: list[dict], request_id: str):
    yield sse({"type": "image_generating", "request_id": request_id})
    try:
        async with async_session() as s:
            prop, n, _bb, variant_gated, _warn = await _resolve_proposal(s, story_id, proposal_id)
            if variant_gated:
                yield sse({"type": "image_failed", "reason": "variant 需先绘制 new_scene 基底", "request_id": request_id})
                return
            scene_slug, kind = prop.scene_slug, prop.kind
            refs = [ReferenceRef.model_validate(r) for r in references]
            res = await picture_from_refs(
                s, story_id=story_id, scene_slug=scene_slug, kind=kind, final_prompt=prompt,
                references=refs, origin="director_b_proposal", source_turn=n,
            )
            await mark_proposal_done(s, proposal_id, res["imagegen_id"])
            await touch_story(s, story_id)
    except ImageGenError as exc:
        yield sse({"type": "image_failed", "reason": str(exc), "request_id": request_id})
        return
    yield sse({"type": "image_ready", "scene": res["scene"], "image_path": res["output_path"],
               "api_call": res["api_call"], "request_id": request_id})


@router.post("/{story_id}/draw/proposal/{proposal_id}/picture")
async def post_picture(story_id: str, proposal_id: int, req: PictureReq) -> StreamingResponse:
    """画图节点(重)出图:用当前提示词 + 用户自由选择的参考图调 gpt-image-2。短命 SSE。
    确认闸门:execute_image 仅经此显式出图路径触达,编辑参考图不开旁路。"""
    request_id = uuid.uuid4().hex
    # 出图作为后台绘图作业:刷新/关页不取消 → 出的图照常落盘(不浪费已花的钱)。
    return start_draw_job(
        story_id,
        f"picture:{proposal_id}",
        _picture_events(story_id, proposal_id, req.prompt, req.references, request_id),
        meta={"kind": "picture", "proposal_id": proposal_id},
    )


# ─────────────────────────────────────────────────────────────
#  替代图片(旁路):不调 gpt-image-2,由用户直接指定一张图作为本次出图结果。
#  选图/上传的动作本身即确认 —— 因为根本没花钱,无需再过确认闸门(真实出图的闸门一律不动)。
#  归属(origin/kind/scene/取代)完全比照真实出图:走提案入口=正典(进黑板),手动入口=草稿(不进)。
# ─────────────────────────────────────────────────────────────


@router.post("/{story_id}/draw/substitute")
async def post_substitute(
    story_id: str,
    proposal_id: int | None = Form(None),
    scene: str | None = Form(None),
    source: str = Form("user_initiated"),
    source_turn: int | None = Form(None),
    imagegen_id: int | None = Form(None),  # ①从过往生成结果里选一张(按 ImageGen.id)
    file: UploadFile | None = File(None),   # ②直接上传一张新图
) -> dict:
    """替代图片:把「指定的已有图」或「上传的新图」复制为本次出图结果落库,跳过 execute_image。

    入口二选一:proposal_id(正典提案,origin=director_b_proposal、scene/kind/轮由提案权威取)
    或 scene(手动绘图,origin=source、kind 按 origin_turn 判)。来源二选一:imagegen_id 或 file。
    """
    if (imagegen_id is None) == (file is None):
        raise HTTPException(400, "需且仅需提供 imagegen_id 或 file 其一")

    async with async_session() as s:
        if await s.get(Story, story_id) is None:
            raise HTTPException(404, "story not found")

        # ── 解析归属:提案制(正典)优先于 手动制 ──
        if proposal_id is not None:
            prop, n, _bb, variant_gated, _warn = await _resolve_proposal(s, story_id, proposal_id)
            scene_slug, kind, origin = prop.scene_slug, prop.kind, "director_b_proposal"
        else:
            if not scene:
                raise HTTPException(400, "需提供 proposal_id 或 scene")
            latest = (
                await s.execute(select(func.max(Turn.turn_index)).where(Turn.story_id == story_id))
            ).scalar()
            n = source_turn if source_turn is not None else latest
            if n is None:
                raise HTTPException(400, "故事尚无回合,无法绘图")
            bb_n = await _blackboard_after_turn(s, story_id, n)
            if bb_n is None:
                raise HTTPException(404, f"turn {n} not found")
            if scene not in (bb_n.get("scenes") or {}):
                raise HTTPException(400, f"scene {scene!r} not in turn {n} blackboard")
            scene_slug, origin = scene, source
            kind = kind_for(bb_n, scene_slug, n)
            if kind is None:
                raise HTTPException(400, f"scene {scene_slug!r} has no origin_turn")
            kinds = await _scene_image_kinds(s, story_id, scene_slug)
            variant_gated = kind == "variant" and "new_scene" not in kinds
        if variant_gated:
            raise HTTPException(409, "variant 需先绘制该场景的 new_scene 基底图")

        # ── 取源图:库内过往结果 或 上传临时文件 ──
        tmp: Path | None = None
        if imagegen_id is not None:
            src = await s.get(ImageGen, imagegen_id)
            if src is None or src.story_id != story_id or not src.output_path:
                raise HTTPException(404, "指定的过往生成图不存在")
            src_abs = BACKEND_ROOT / src.output_path
            if not src_abs.exists():
                raise HTTPException(404, "过往生成图文件已丢失")
        else:
            suffix = Path(file.filename or "").suffix or ".png"
            tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
            tmp.write_bytes(await file.read())
            src_abs = tmp

        try:
            res = await substitute_picture(
                s, story_id=story_id, scene_slug=scene_slug, kind=kind,
                origin=origin, source_turn=n, src_abs=src_abs,
            )
            await touch_story(s, story_id)
            if proposal_id is not None:  # 提案入口:替代图也算把该提案画完
                await mark_proposal_done(s, proposal_id, res["imagegen_id"])
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)  # 已复制进库,临时上传文件删掉

    return {"type": "image_substituted", **res, "kind": kind, "draw_turn": n, "proposal_id": proposal_id}


# ─────────────────────────────────────────────────────────────
#  手动指定绘图(用户自建提案):让用户为「任意场景 × 任意轮」自己创建一条 DrawProposal。
#  作者是用户而非导演 B,但落地后与 B 的提案**完全同一条管线**:进绘图台待办、在该轮的导演
#  工作台显示为绘图分支、写稿/画图节点照常、画完进黑板并在场景地图可见。上下文「就好像在那一轮
#  绘图」由既有的按 origin_proposal_turn 截断逻辑保证。kind 按场景诞生点权威判定。
# ─────────────────────────────────────────────────────────────


@router.get("/{story_id}/turn/{turn_index}/scenes")
async def get_turn_scenes(story_id: str, turn_index: int) -> dict:
    """第 N 轮黑板里**存在且可画**的场景(供手动指定 picker:先定轮 → 列该轮场景)。
    每个场景附:在该轮画它会是什么 kind(按诞生点)、variant 是否因缺基底而被门控。
    无诞生点(origin_turn 缺失)的场景不可画,直接略去。"""
    async with async_session() as s:
        if await s.get(Story, story_id) is None:
            raise HTTPException(404, "story not found")
        bb_n = await _blackboard_after_turn(s, story_id, turn_index)
        if bb_n is None:
            raise HTTPException(404, f"turn {turn_index} not found")
        scenes_bb = bb_n.get("scenes") or {}
        out: list[dict] = []
        for slug, sc in scenes_bb.items():
            if not isinstance(sc, dict):
                continue
            kind = kind_for(bb_n, slug, turn_index)
            if kind is None:
                continue  # 无诞生点 → 该轮无法判 kind,不可画
            kinds = await _scene_image_kinds(s, story_id, slug)
            gated = kind == "variant" and "new_scene" not in kinds
            out.append({"slug": slug, "name": sc.get("name", slug), "kind": kind, "variant_gated": gated})
    return {"turn_index": turn_index, "scenes": out}


class CreateProposalReq(BaseModel):
    scene: str = Field(min_length=1)
    turn: int  # 挂到哪一轮(origin_proposal_turn);上下文据此轮截断


@router.post("/{story_id}/proposal")
async def post_create_proposal(story_id: str, req: CreateProposalReq) -> dict:
    """手动指定:用户自建一条绘图提案,挂到第 N 轮。返回新建的提案行(随即出现在绘图台/工作台)。

    校验:场景须存在于第 N 轮黑板且有诞生点(否则无法判 kind)。kind 后端权威判定。
    不做去重:与 B 既有提案、与同场景同轮的其它提案可并存(各是一条独立待办)。
    reason 记「(手动指定)」以便前端区分作者。
    """
    async with async_session() as s:
        if await s.get(Story, story_id) is None:
            raise HTTPException(404, "story not found")
        bb_n = await _blackboard_after_turn(s, story_id, req.turn)
        if bb_n is None:
            raise HTTPException(404, f"turn {req.turn} not found")
        if req.scene not in (bb_n.get("scenes") or {}):
            raise HTTPException(400, f"scene {req.scene!r} not in turn {req.turn} blackboard")
        kind = kind_for(bb_n, req.scene, req.turn)
        if kind is None:
            raise HTTPException(400, f"scene {req.scene!r} has no origin_turn")
        prop = DrawProposal(
            story_id=story_id, scene_slug=req.scene, origin_proposal_turn=req.turn,
            kind=kind, status="pending", reason="(手动指定)",
        )
        s.add(prop)
        await s.commit()
        await s.refresh(prop)
        await touch_story(s, story_id)
    return {
        "id": prop.id, "scene_slug": prop.scene_slug, "kind": prop.kind,
        "status": prop.status, "origin_proposal_turn": prop.origin_proposal_turn, "reason": prop.reason,
    }
