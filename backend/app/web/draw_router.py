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
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.assets.reference_store import list_references
from app.db.models import Blackboard, Story
from app.db.session import async_session
from app.imaging.draw_service import DraftBundle, apply_decision, prepare_draft
from app.imaging.executor import ImageGenError, ResolvedRefs, resolve_references
from app.models.schemas import ReferenceRef
from app.stories.store import touch_story
from app.web.sse import SSE_HEADERS, sse

router = APIRouter(prefix="/story", tags=["draw"])


@dataclass
class _PendingDraft:
    """/draw 与 /draw/confirm 之间暂存的、用户已审阅的稿。confirm 作用于这同一份,
    保证「确认的就是审阅过的那张」——这是确认闸门成立的前提。"""

    bundle: DraftBundle
    story_id: str
    origin: str
    source_turn: int | None


# 进程内暂存:draft_id → 已审阅的稿。一次性,确认/复用/跳过后即弹出。
_PENDING: dict[str, _PendingDraft] = {}


class DrawReq(BaseModel):
    scene: str = Field(min_length=1)
    source: str = "user_initiated"  # user_initiated | director_b_proposal(写入 ImageGen.origin)
    request: str | None = None  # 绘图请求文本(B 提案可带其描述;默认据场景生成)
    source_turn: int | None = None


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
    """阶段①:绘图 Agent 写稿。不出图、不花钱,可同步返回。"""
    async with async_session() as s:
        if await s.get(Story, story_id) is None:
            raise HTTPException(404, "story not found")
        bb_row = await s.get(Blackboard, story_id)
        blackboard = json.loads(bb_row.json_blob) if bb_row else {}
        if req.scene not in (blackboard.get("scenes") or {}):
            raise HTTPException(400, f"scene {req.scene!r} not in blackboard")
        request = req.request or f"为当前场景 {req.scene} 画一张图,定格本回合的画面。"
        bundle = await prepare_draft(
            s, story_id=story_id, blackboard=blackboard, scene_slug=req.scene, draw_request=request
        )
        assets = await list_references(s, story_id)

    draft_id = uuid.uuid4().hex
    _PENDING[draft_id] = _PendingDraft(
        bundle=bundle, story_id=story_id, origin=req.source, source_turn=req.source_turn
    )
    return {
        "type": "draft_ready",
        "draft_id": draft_id,
        "scene": bundle.scene_slug,
        "kind": bundle.draft.kind,
        "prompt_text": bundle.draft.prompt_text,  # 可编辑;confirm 时回传
        "refs": _refs_payload(bundle, {a.id: a for a in assets}),
        "history": [{"semantic_name": h["semantic_name"], "image_path": h["image_path"]} for h in bundle.history],
        # 库里可加的参考图(供用户在 confirm 前往清单里添加;M5 据此渲染「可添加」列表)
        "library": [
            {"asset_id": a.id, "label": a.label, "description": a.description,
             "category": a.category, "file_path": a.file_path}
            for a in assets
        ],
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
        return StreamingResponse(
            _confirm_events(pending, final_prompt, request_id, resolved),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
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
        return res

    raise HTTPException(400, f"unknown decision: {req.decision!r}")
