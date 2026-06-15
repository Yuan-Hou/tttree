"""回合推进(临时 SSE 流)+ 快照(普通 HTTP)。

文本线:POST /story/{id}/turn 开一条 SSE,推 turn_started → narrative_token* →
narrative_done → state_updated → draw_proposed? → turn_done,推完即关。
内部就是已验证的 A→Writer→B→reducer,逻辑不动;回合内顺序约束保留(A/Writer/B 看同一份
本回合之前的黑板与历史;reducer 三调后才写库)。

恢复不靠 SSE,靠 GET /story/{id}/snapshot 拿完整黑板 + 历史。
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agents.context import Message, build_messages
from app.agents.director import DirectorOutputError, run_director
from app.agents.director_review import DirectorReviewError, run_director_review
from app.agents.writer import stream_writer
from app.db.models import Blackboard, ImageGen, Story, Turn
from app.db.session import async_session
from app.imaging.pipeline import DRAFT_ORIGIN
from app.knowledge.store import get_knowledge
from app.state.reducer import reduce_turn
from app.stories.settings_store import get_or_create_settings, resolve_agent_model
from app.stories.store import touch_story
from app.web.sse import SSE_HEADERS, sse

router = APIRouter(prefix="/story", tags=["turn"])


class TurnReq(BaseModel):
    user_input: str = Field(min_length=1)


async def _load_history(session, story_id: str) -> list[Message]:
    """从 Turn 表重建「干净」历史(user=玩家输入, assistant=叙事),供 build_messages 用。
    Web 每回合无状态,历史从 DB 重建——与「快照恢复」同源,保证缓存前缀稳定。"""
    turns = (
        await session.execute(
            select(Turn).where(Turn.story_id == story_id).order_by(Turn.turn_index)
        )
    ).scalars().all()
    history: list[Message] = []
    for t in turns:
        history.append({"role": "user", "content": t.user_input})
        history.append({"role": "assistant", "content": t.narrative})
    return history


async def _turn_events(story_id: str, user_input: str) -> AsyncIterator[str]:
    # 载入本回合之前的黑板与历史(A/Writer/B 共享这同一份)
    async with async_session() as s:
        story = await s.get(Story, story_id)
        if story is None:
            yield sse({"type": "error", "reason": "story not found"})
            return
        bb_row = await s.get(Blackboard, story_id)
        blackboard = json.loads(bb_row.json_blob)
        history = await _load_history(s, story_id)
        knowledge = await get_knowledge(s, story_id)  # 仅注入 Director-A 的设定底座
        # 故事内模型设置:各 agent 按「覆盖 → 全局默认」解析出实际模型 id(默认全 deepseek)。
        st = await get_or_create_settings(s, story_id)
        model_a = resolve_agent_model(st, "director_a")
        model_w = resolve_agent_model(st, "writer")
        model_b = resolve_agent_model(st, "director_b")
        last_idx = (
            await s.execute(select(func.max(Turn.turn_index)).where(Turn.story_id == story_id))
        ).scalar() or 0
    next_idx = last_idx + 1

    # 三次调用的完整 messages 各构造一次,既喂给 LLM、又原样存档(M4.5-B)。
    # 用 build_messages 预构造后传给 agent 复用 → 存下的就是真正喂进去的那份,零偏差;
    # build_messages 逻辑/缓存不受影响(只是多存一份)。回合内顺序约束不变。
    # ---- Director-A(读黑板 + 设定参考)----
    a_messages = build_messages(
        "director", history=history, blackboard=blackboard, user_action=user_input, knowledge=knowledge
    )
    try:
        a = await run_director(history, blackboard, user_input, knowledge=knowledge, messages=a_messages, model=model_a)
    except DirectorOutputError as exc:
        yield sse({"type": "error", "reason": f"director-a: {exc}"})
        return

    yield sse({"type": "turn_started", "turn_index": next_idx})

    # ---- Writer(逐 token 推)----
    w_messages = build_messages(
        "writer", history=history, blackboard=blackboard, user_action=user_input, writing_brief=a.writing_brief
    )
    chunks: list[str] = []
    async for tok in stream_writer(history, blackboard, user_input, a.writing_brief, messages=w_messages, model=model_w):
        chunks.append(tok)
        yield sse({"type": "narrative_token", "text": tok})
    narrative = "".join(chunks)
    yield sse({"type": "narrative_done", "full_narrative": narrative})

    # ---- Director-B ----
    b_messages = build_messages(
        "director_review", history=history, blackboard=blackboard, user_action=user_input,
        narrative=narrative, director_a_plan=a.model_dump(),
    )
    try:
        new_bb = await run_director_review(
            history, blackboard, user_input, narrative, director_a_plan=a.model_dump(), messages=b_messages, model=model_b
        )
    except DirectorReviewError as exc:
        yield sse({"type": "error", "reason": f"director-b: {exc}"})
        return

    # ---- reducer(三调全部完成后才写库)----
    async with async_session() as s:
        result = await reduce_turn(
            story_id=story_id,
            director_b_new_blackboard_str=json.dumps(new_bb, ensure_ascii=False),
            writer_narrative=narrative,
            director_a_json=a.model_dump_json(),
            user_input=user_input,
            session=s,
            director_a_messages=json.dumps(a_messages, ensure_ascii=False),
            writer_messages=json.dumps(w_messages, ensure_ascii=False),
            director_b_messages=json.dumps(b_messages, ensure_ascii=False),
        )
        await touch_story(s, story_id)

    yield sse({"type": "state_updated", "blackboard": result.blackboard, "beat_title": result.beat_title})
    if result.draw_proposals:
        yield sse({"type": "draw_proposed", "proposals": result.draw_proposals})
    yield sse({"type": "turn_done"})


@router.post("/{story_id}/turn")
async def post_turn(story_id: str, req: TurnReq) -> StreamingResponse:
    return StreamingResponse(
        _turn_events(story_id, req.user_input),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/{story_id}/snapshot")
async def get_snapshot(story_id: str) -> dict:
    async with async_session() as s:
        story = await s.get(Story, story_id)
        if story is None:
            raise HTTPException(404, "story not found")
        bb_row = await s.get(Blackboard, story_id)
        blackboard = json.loads(bb_row.json_blob) if bb_row else {}
        turns = (
            await s.execute(
                select(Turn).where(Turn.story_id == story_id).order_by(Turn.turn_index)
            )
        ).scalars().all()
        # 用户手动草稿图(origin=user_initiated):不在黑板里,单独从 ImageGen 按场景取出,
        # 供「场景与画」标注为「非正式」展示。正典图(进黑板)走 scenes_images。
        draft_rows = (
            await s.execute(
                select(ImageGen)
                .where(
                    ImageGen.story_id == story_id,
                    ImageGen.origin == DRAFT_ORIGIN,
                    ImageGen.output_path != "",
                )
                .order_by(ImageGen.id)
            )
        ).scalars().all()

    scenes_images = {
        slug: scene.get("image_paths", [])
        for slug, scene in (blackboard.get("scenes") or {}).items()
    }
    scenes_drafts: dict[str, list[str]] = {}
    for ig in draft_rows:
        scenes_drafts.setdefault(ig.scene_slug, []).append(ig.output_path)
    return {
        "story_id": story_id,
        "title": story.title,
        "blackboard": blackboard,
        "scenes_images": scenes_images,
        "scenes_drafts": scenes_drafts,
        "history": [
            {
                "turn_index": t.turn_index,
                "user_input": t.user_input,
                "narrative": t.narrative,
                "beat_title": t.beat_title,
            }
            for t in turns
        ],
    }
