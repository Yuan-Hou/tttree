"""绘图人在回路的共享服务:写稿(阶段①)、格式化审阅文本(阶段②素材)、应用用户决策
(阶段③或 reuse/skip)。m1_cli 的交互层与验收脚本都复用它,保证两入口走同一套逻辑。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.illustrator import render_reference_catalog, run_illustrator
from app.assets.reference_store import list_references
from app.db.models import ImageGen
from app.imaging.executor import ImageGenError, ResolvedRefs, execute_image, resolve_references
from app.imaging.pipeline import record_generation
from app.models.schemas import IllustratorDraft

_HISTORY_TAGS = ["初见", "再访", "其后"]


async def build_history_catalog(
    session: AsyncSession, story_id: str, scene_slug: str, scene_name: str
) -> list[dict]:
    """把该场景已有的历史生成图,用『场景名+状态』式语义名列出(不用位置序号)。"""
    rows = (
        await session.execute(
            select(ImageGen)
            .where(
                ImageGen.story_id == story_id,
                ImageGen.scene_slug == scene_slug,
                ImageGen.kind != "reuse",
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


@dataclass
class DraftBundle:
    scene_slug: str
    draft: IllustratorDraft
    resolved: ResolvedRefs
    history: list[dict]


async def prepare_draft(
    session: AsyncSession,
    *,
    story_id: str,
    blackboard: dict,
    scene_slug: str,
    draw_request: str,
) -> DraftBundle:
    """阶段①:绘图 Agent 据黑板 + 画风圣经 + 参考图库清单写稿。"""
    assets = await list_references(session, story_id)
    scene = blackboard["scenes"][scene_slug]
    history = await build_history_catalog(session, story_id, scene_slug, scene.get("name", scene_slug))
    catalog = render_reference_catalog(assets, history_images=history)
    draft = await run_illustrator(
        history=[], blackboard=blackboard, draw_request=draw_request, reference_catalog=catalog
    )
    resolved = resolve_references(draft.reference_manifest, {a.id: a for a in assets})
    return DraftBundle(scene_slug=scene_slug, draft=draft, resolved=resolved, history=history)


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
        result = await execute_image(
            final_prompt=final_prompt, ref_files=use_refs.files, scene_slug=bundle.scene_slug
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
