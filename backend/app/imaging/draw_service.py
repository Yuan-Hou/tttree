"""绘图人在回路的共享服务:写稿(阶段①)、格式化审阅文本(阶段②素材)、应用用户决策
(阶段③或 reuse/skip)。m1_cli 的交互层与验收脚本都复用它,保证两入口走同一套逻辑。"""

import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.bibles import resolve_visual_style_bible
from app.agents.illustrator import build_illustrator_messages, render_reference_catalog, run_illustrator
from app.assets.reference_store import list_references
from app.db.models import ImageGen
from app.imaging.executor import ImageGenError, ResolvedRefs, execute_image, resolve_references
from app.imaging.pipeline import DRAFT_ORIGIN, is_canon_origin, record_generation
from app.models.schemas import IllustratorDraft, ReferenceRef
from app.stories.settings_store import (
    get_or_create_settings,
    resolve_agent_model,
    resolve_image_model,
)
from app.storage import BACKEND_ROOT, IMAGES_SUBDIR

_HISTORY_TAGS = ["初见", "再访", "其后"]


async def build_history_catalog(
    session: AsyncSession, story_id: str, scene_slug: str, scene_name: str
) -> list[dict]:
    """绘图 Agent 的**自动候选池**:该场景已有的历史生成图,用『场景名+状态』式语义名列出。

    绘图归属(本次修整):排除 origin=user_initiated 的用户手动草稿 —— 手动图对写稿 Agent 隐身,
    不进它的连贯参考候选。这是「喂 Agent 的候选(排手动图)」一侧;「给用户手动选的过往结果列表」
    是另一条独立查询(draw_router.get_proposal_draw 的 past_images,全列、不过滤),两池子分开。
    """
    rows = (
        await session.execute(
            select(ImageGen)
            .where(
                ImageGen.story_id == story_id,
                ImageGen.scene_slug == scene_slug,
                ImageGen.kind != "reuse",
                ImageGen.origin != DRAFT_ORIGIN,  # 手动草稿不进 Agent 候选池
                ImageGen.superseded.is_(False),  # 被取代的旧正典图自动退出候选池
            )
            .order_by(ImageGen.id)
        )
    ).scalars().all()
    out = []
    for i, ig in enumerate(rows):
        tag = _HISTORY_TAGS[i] if i < len(_HISTORY_TAGS) else f"状态{i + 1}"
        out.append(
            {
                "semantic_name": f"{scene_name}·{tag}",
                "image_path": ig.output_path,
                "note": f"本场景已生成的图(kind={ig.kind}),用于保持同一空间的布局/视觉锚点连贯。",
            }
        )
    return out


def _backfill_history_paths(manifest: list[ReferenceRef], history_images: list[dict]) -> None:
    """绘图 Agent 现在只凭语义名引用历史图(其视野里已无原始 image_path)。这里按语义名把真实
    路径权威回填进 manifest —— 真实路径由后端掌握,Agent 写错/写空都不经手它。匹配不到的历史图
    项 image_path 置空,下游(RefPicker 预选 / 出图解析)自然跳过,绝不误用 Agent 杜撰的路径。"""
    by_name = {h["semantic_name"]: h["image_path"] for h in history_images}
    for r in manifest:
        if r.source == "history_image":
            r.image_path = by_name.get(r.semantic_name)


@dataclass
class DraftBundle:
    scene_slug: str
    draft: IllustratorDraft
    resolved: ResolvedRefs
    history: list[dict]


def _kind_hint(kind: str | None) -> str:
    """把后端权威判定的绘图类型告诉绘图 Agent,让它写对应风格的提示词、为 variant 选基底图。"""
    if kind == "variant":
        return (
            "\n\n【绘图类型已定:variant 同场景变体】请基于本场景已有的历史生成图做变体,"
            "在引用清单里引用作为基底的那张历史图,保持空间布局/视觉锚点连贯。"
        )
    if kind == "new_scene":
        return "\n\n【绘图类型已定:new_scene 本场景基底图】这是该场景的第一张图,文生图建立基底视觉。"
    return ""


async def prepare_draft(
    session: AsyncSession,
    *,
    story_id: str,
    blackboard: dict,
    scene_slug: str,
    draw_request: str,
    history: list | None = None,
    kind: str | None = None,
    tips: list[str] | None = None,
    extra_instruction: str | None = None,
) -> DraftBundle:
    """阶段①:绘图 Agent 据黑板 + 画风圣经 + 参考图库清单写稿。

    history / blackboard 由调用方按「该绘图提案所属轮 N」截断后传入(≤N 的对话 + 第 N 轮
    blackboard_after),避免把后续剧情画进早期场景图。kind 为后端权威判定值(按 origin_turn),
    传入后:① 作为提示告知 Agent ② 覆盖 Agent 自报的 kind,确保存档 kind 与规则一致。
    tips:第 N 轮导演 A 的设定提示(由调用方按归属轮取出),递入易变区尾部。
    """
    assets = await list_references(session, story_id)
    scene = blackboard["scenes"][scene_slug]
    history_imgs = await build_history_catalog(session, story_id, scene_slug, scene.get("name", scene_slug))
    catalog = render_reference_catalog(assets, history_images=history_imgs)
    st = await get_or_create_settings(session, story_id)
    model = resolve_agent_model(st, "illustrator")  # 绘图写稿 Agent 的故事内模型设置
    draft = await run_illustrator(
        history=history or [],
        blackboard=blackboard,
        draw_request=draw_request + _kind_hint(kind),
        reference_catalog=catalog,
        visual_style=resolve_visual_style_bible(st.visual_style_bible),  # 故事自定义画风圣经(空则全局默认)
        model=model,
        tips=tips,
        extra_instruction=extra_instruction,
    )
    if kind in ("new_scene", "variant"):
        draft.kind = kind  # 后端权威覆盖
    _backfill_history_paths(draft.reference_manifest, history_imgs)  # 语义名 → 真实路径(后端权威)
    resolved = resolve_references(draft.reference_manifest, {a.id: a for a in assets})
    return DraftBundle(scene_slug=scene_slug, draft=draft, resolved=resolved, history=history_imgs)


async def write_illustration_draft(
    session: AsyncSession,
    *,
    story_id: str,
    blackboard: dict,
    scene_slug: str,
    draw_request: str,
    history: list,
    kind: str | None,
    messages: list | None = None,
    tips: list[str] | None = None,
    extra_instruction: str | None = None,
) -> tuple[list, IllustratorDraft]:
    """写稿步(绘图 Agent / DeepSeek)。返回 (喂进去的完整 messages, 写出的稿)。

    messages 给定(用户编辑过的写稿输入)则原样重跑;否则按截断上下文构造。kind 为后端权威值,
    覆盖 Agent 自报。供「写稿节点」持久化输入+输出、并支持「重写提示词」。
    tips:第 N 轮导演 A 的设定提示(由调用方按归属轮取出),递入易变区尾部;复用既有 messages 时
    其中已含 tips,不再重复注入。
    extra_instruction:用户填的「附加指令」,仅在新建 messages 时原样接到易变区最末尾;复用既有
    messages 时其中已含(或由用户在原始 messages 里自行掌控),不再重复注入。
    """
    assets = await list_references(session, story_id)
    scene = blackboard["scenes"][scene_slug]
    hist_imgs = await build_history_catalog(session, story_id, scene_slug, scene.get("name", scene_slug))
    catalog = render_reference_catalog(assets, history_images=hist_imgs)
    full_request = draw_request + _kind_hint(kind)
    st = await get_or_create_settings(session, story_id)
    model = resolve_agent_model(st, "illustrator")  # 绘图写稿 Agent 的故事内模型设置
    used = messages or build_illustrator_messages(
        history=history, blackboard=blackboard, draw_request=full_request, reference_catalog=catalog,
        visual_style=resolve_visual_style_bible(st.visual_style_bible),  # 故事自定义画风圣经(空则全局默认)
        tips=tips, extra_instruction=extra_instruction,
    )
    draft = await run_illustrator(
        history=history, blackboard=blackboard, draw_request=full_request,
        reference_catalog=catalog, messages=used, model=model,
    )
    if kind in ("new_scene", "variant"):
        draft.kind = kind
    _backfill_history_paths(draft.reference_manifest, hist_imgs)  # 语义名 → 真实路径(后端权威)
    return used, draft


async def picture_from_refs(
    session: AsyncSession,
    *,
    story_id: str,
    scene_slug: str,
    kind: str,
    final_prompt: str,
    references: list[ReferenceRef],
    origin: str,
    source_turn: int | None,
) -> dict:
    """画图步(gpt-image-2)。据用户确认的提示词 + 自由选择的参考图(图库 + 过往结果)出图。

    这是触达 gpt-image-2 的路径,由「画图节点/绘图台出图」的显式确认动作调用 —— 确认闸门保留、无旁路。
    ImageGen 记录用户最终所选参考图(图库→ref_asset_ids,过往结果→ref_image_paths),审计反映实际所用。
    """
    assets = await list_references(session, story_id)
    resolved = resolve_references(references, {a.id: a for a in assets})
    st = await get_or_create_settings(session, story_id)
    result = await execute_image(
        final_prompt=final_prompt, ref_files=resolved.files, scene_slug=scene_slug,
        image_model=resolve_image_model(st),  # 故事所选绘图模型(空则全局默认 gpt-image-2)
    )
    ig = await record_generation(
        session,
        story_id=story_id,
        scene_slug=scene_slug,
        kind=kind,
        final_prompt=final_prompt,
        ref_asset_ids=resolved.asset_ids,
        ref_image_paths=resolved.image_paths,
        output_path=result.output_path,
        origin=origin,
        source_turn=source_turn,
        append_to_blackboard=is_canon_origin(origin),  # 正典进黑板;手动草稿不进
    )
    return {"scene": scene_slug, "output_path": result.output_path, "api_call": result.api_call, "imagegen_id": ig.id}


async def substitute_picture(
    session: AsyncSession,
    *,
    story_id: str,
    scene_slug: str,
    kind: str,
    origin: str,
    source_turn: int | None,
    src_abs: Path,
) -> dict:
    """替代图片(旁路):**不调** gpt-image-2,直接把用户指定的一张图当作本次出图结果落库。

    src_abs:源图绝对路径——可来自「过往生成结果」的库内文件,或用户上传图的临时文件。
    无论哪种来源,都**复制一份新文件**落到 storage/images/(不引用原文件路径,避免日后原记录被
    清理时牵连),再走与真实出图完全一致的 record_generation:kind 仍是 new_scene/variant(非 reuse)、
    按 origin 决定进不进黑板、同 (scene, source_turn) 取代旧正典图。只省掉 API 调用,落库/归属语义不变。
    """
    (BACKEND_ROOT / IMAGES_SUBDIR).mkdir(parents=True, exist_ok=True)
    out_rel = f"{IMAGES_SUBDIR}/{scene_slug}_{uuid.uuid4().hex[:8]}.png"
    shutil.copy2(src_abs, BACKEND_ROOT / out_rel)  # 复制成新文件,与原图彻底解耦
    ig = await record_generation(
        session,
        story_id=story_id,
        scene_slug=scene_slug,
        kind=kind,
        final_prompt="(替代图片:用户直接指定,未经 gpt-image-2)",
        ref_asset_ids=[],
        ref_image_paths=[],
        output_path=out_rel,
        origin=origin,
        source_turn=source_turn,
        append_to_blackboard=is_canon_origin(origin),  # 正典进黑板;手动草稿不进——比照真实出图
    )
    return {"scene": scene_slug, "output_path": out_rel, "api_call": "substitute", "imagegen_id": ig.id}


def format_review(bundle: DraftBundle, final_prompt: str | None = None) -> str:
    """阶段②素材:全程语义名的审阅文本(供用户查看/编辑)。"""
    d = bundle.draft
    lines = [f"绘图类型 kind = {d.kind}", "", "— 提示词文本(可编辑)—", final_prompt or d.prompt_text]
    lines += ["", "— 引用清单(语义名 → 来源)—"]
    for r in d.reference_manifest:
        loc = f"asset_id={r.asset_id}" if r.source == "reference_asset" else f"history={r.image_path}"
        lines.append(f"  · 「{r.semantic_name}」[{r.source}; {loc}]")
        lines.append(f"      用途: {r.purpose}")
    if bundle.history:
        lines.append("\n(可用历史图:" + ", ".join(h["semantic_name"] for h in bundle.history) + ")")
    return "\n".join(lines)


async def apply_decision(
    session: AsyncSession,
    *,
    decision: str,  # confirm(出图,花钱)/ reuse(复用已有图,不花钱)/ skip(跳过)
    bundle: DraftBundle,
    final_prompt: str,
    story_id: str,
    origin: str,
    source_turn: int | None = None,
    reuse_image_path: str | None = None,
    resolved: ResolvedRefs | None = None,
) -> dict:
    # 用户在 confirm 前可编辑引用清单(增删参考图);传入 resolved 即「用户最终确认的清单」,
    # 执行层据此传图、ImageGen 也记这一份(审计反映实际用了什么,不是 Agent 原始建议)。
    use_refs = resolved if resolved is not None else bundle.resolved

    if decision == "skip":
        return {"action": "skip", "scene": bundle.scene_slug}

    if decision == "reuse":
        # 关联一张已有图,不调用 gpt-image-2、不花钱。复用图已在 image_paths 中,不重复追加。
        path = reuse_image_path or (bundle.history[0]["image_path"] if bundle.history else "")
        ig = await record_generation(
            session,
            story_id=story_id,
            scene_slug=bundle.scene_slug,
            kind="reuse",
            final_prompt=final_prompt,
            ref_asset_ids=[],
            ref_image_paths=[path] if path else [],
            output_path=path,
            origin=origin,
            source_turn=source_turn,
            append_to_blackboard=False,
        )
        return {"action": "reuse", "scene": bundle.scene_slug, "image_path": path, "imagegen_id": ig.id}

    if decision == "confirm":  # 真出图(花钱)
        st = await get_or_create_settings(session, story_id)
        result = await execute_image(
            final_prompt=final_prompt, ref_files=use_refs.files, scene_slug=bundle.scene_slug,
            image_model=resolve_image_model(st),  # 故事所选绘图模型(空则全局默认 gpt-image-2)
        )
        ig = await record_generation(
            session,
            story_id=story_id,
            scene_slug=bundle.scene_slug,
            kind=bundle.draft.kind,
            final_prompt=final_prompt,
            ref_asset_ids=use_refs.asset_ids,
            ref_image_paths=use_refs.image_paths,
            output_path=result.output_path,
            origin=origin,
            source_turn=source_turn,
            append_to_blackboard=is_canon_origin(origin),  # 正典进黑板;手动草稿(user_initiated)不进
        )
        return {
            "action": "confirm",
            "scene": bundle.scene_slug,
            "api_call": result.api_call,
            "output_path": result.output_path,
            "imagegen_id": ig.id,
        }

    raise ValueError(f"未知 decision: {decision}")


def confirm_loop(
    bundle: DraftBundle,
    final_prompt: str,
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> tuple[str, str]:
    """唯一的人在回路确认门(带编辑能力)。返回 (decision, final_prompt)。

    铁律:**只有**用户键入 `y` 才会返回 `confirm`(下游才会真正调 gpt-image-2 花钱)。
    `e` 可反复编辑提示词;`r`=复用已有图(不花钱);`s`=跳过;其他输入一律不放行、继续询问。
    没有任何参数能让本函数在未输入 `y` 的情况下返回 `confirm`——即无旁路。
    """
    print_fn(format_review(bundle, final_prompt))
    while True:
        ans = (input_fn("决策 [y=出图(花钱) / e=编辑提示词 / r=复用已有图 / s=跳过]: ") or "").strip().lower()
        if ans == "e":
            print_fn("输入新的提示词文本(单行),回车确认:")
            edited = (input_fn("> ") or "").strip()
            if edited:
                final_prompt = edited
                print_fn("[已编辑提示词]")
            continue
        if ans in {"y", "r", "s"}:
            return {"y": "confirm", "r": "reuse", "s": "skip"}[ans], final_prompt
        # 无法识别的输入:不放行,继续询问(杜绝误触直接出图)


async def interactive_draw_session(
    session_factory: Callable[[], AsyncSession],
    *,
    story_id: str,
    blackboard: dict,
    scene_slug: str,
    draw_request: str,
    origin: str,
    source_turn: int | None = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> dict | None:
    """单张图的完整人在回路会话:写稿 → 审阅(可编辑)→ 确认门 → 执行/复用/跳过。

    这是触达 gpt-image-2 的**唯一**面向用户入口;两个入口(B 提案、用户主动)都走这里,
    每次只处理一张图,confirm 必经 confirm_loop 的 `y`。
    """
    if scene_slug not in blackboard.get("scenes", {}):
        print_fn(f"[draw] 场景 {scene_slug!r} 不在黑板中,已跳过。")
        return None

    async with session_factory() as s:
        bundle = await prepare_draft(
            s, story_id=story_id, blackboard=blackboard, scene_slug=scene_slug, draw_request=draw_request
        )

    print_fn("\n--- 【绘图·人在回路】请审阅(全程语义名)---")
    decision, final_prompt = confirm_loop(bundle, bundle.draft.prompt_text, input_fn=input_fn, print_fn=print_fn)

    try:
        async with session_factory() as s:
            res = await apply_decision(
                s,
                decision=decision,
                bundle=bundle,
                final_prompt=final_prompt,
                story_id=story_id,
                origin=origin,
                source_turn=source_turn,
            )
    except ImageGenError as exc:
        print_fn(f"❌ 出图失败(已捕获,未崩溃): {exc}\n可改稿/换图后重试。")
        return {"action": "error", "error": str(exc)}

    print_fn(f"[draw] {res}")
    return res
