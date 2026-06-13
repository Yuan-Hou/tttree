"""M3-B 验收:绘图 Agent 写「带明确参考图用途说明的提示词稿」(DeepSeek,不出图、不花钱)。

构造一个有两名已登记角色在场的场景,让 Agent 写稿,贴出 ①提示词文本 ②引用清单,
并自动核查:无位置序号、图文指代对应、参考图用途明确、画风融入而非照抄。
用法(backend/ 下):python -m scripts.m3b_verify
"""

import asyncio
import json
import re

from app.agents.illustrator import (
    STYLE_COPY_MIN_SPAN,
    longest_style_copy_span,
    render_reference_catalog,
    run_illustrator,
)
from app.assets.reference_store import list_references
from app.db.session import async_session, create_all, engine

STORY = "cli-story"  # 参考图库登记所用的 story_id

# 一个让两名已登记角色(爱丽丝、优香)在场的场景,以触发对每张立绘的引用与用途说明
DEMO_BLACKBOARD = {
    "story_meta": {"title": "薄暮教室", "current_scene": "old_classroom", "latest_beat": "黄昏对峙"},
    "scenes": {
        "old_classroom": {
            "name": "废弃的旧教室",
            "base_prompt": "黄昏时分一间废弃的旧教室,翻倒的课桌椅,积灰的黑板,夕照从破碎的窗斜射进来。",
            "visual_anchors": ["破碎的窗户", "积灰的旧黑板", "翻倒的课桌椅"],
            "state": "夕照穿过破窗,尘埃在光柱里浮动;教室中央,两人正面对面僵立,空气紧绷。",
            "connections": [],
            "image_paths": [],
        }
    },
    "characters": {
        "爱丽丝": {
            "location": "old_classroom",
            "status": "背着巨大的白色武器,神情认真而执拗,不肯退让",
            "inventory": [],
            "relations": {"优香": "同伴,但此刻意见相左"},
            "appearance": "深蓝渐变长发,蓝色眼睛,白黑蓝配色的学生制服外套,头顶青绿色方形光环,背着巨大的白色科幻武器",
        },
        "优香": {
            "location": "old_classroom",
            "status": "双臂抱胸,眉头紧蹙,语气冷静却带着不满",
            "inventory": [],
            "relations": {"爱丽丝": "同伴,正试图劝阻她"},
            "appearance": "深紫色长发双马尾,蓝紫色眼睛,黑色西装式制服与白衬衫、蓝色领带,头顶蓝色光环",
        },
    },
    "items": {},
    "notes": [],
}

DRAW_REQUEST = "为当前场景 old_classroom 画一张新场景图,定格爱丽丝与优香在黄昏旧教室里正面对峙的瞬间。"

FORBIDDEN_ORDINALS = ["第一张", "第二张", "第三张", "第四张", "第五张", "第1张", "第2张", "image_1", "image_2", "image_3"]


def check(label: str, ok: bool) -> None:
    print(f"  {'✅' if ok else '❌'} {label}")


async def main() -> None:
    await create_all(engine)
    async with async_session() as s:
        assets = await list_references(s, STORY)

    if not assets:
        print("⚠️  参考图库为空,请先用 refs_cli 登记角色立绘再跑本验收。")
        return

    catalog = render_reference_catalog(assets, history_images=[])
    print("=" * 72)
    print("喂给绘图 Agent 的【参考图库清单】")
    print("=" * 72)
    print(catalog)

    draft = await run_illustrator(
        history=[],
        blackboard=DEMO_BLACKBOARD,
        draw_request=DRAW_REQUEST,
        reference_catalog=catalog,
    )

    print("\n" + "=" * 72)
    print("① 提示词文本(prompt_text)")
    print("=" * 72)
    print(draft.prompt_text)

    print("\n" + "=" * 72)
    print(f"② 引用清单(reference_manifest)  kind={draft.kind}")
    print("=" * 72)
    for r in draft.reference_manifest:
        print(json.dumps(r.model_dump(), ensure_ascii=False, indent=2))

    print("\n" + "=" * 72)
    print("自动核查")
    print("=" * 72)
    text = draft.prompt_text
    names = [r.semantic_name for r in draft.reference_manifest]

    check(f"kind 合法(实得 {draft.kind!r})", draft.kind in ("new_scene", "variant", "reuse"))
    hit_ord = [w for w in FORBIDDEN_ORDINALS if w in text or any(w in n for n in names)]
    check(f"全程无位置序号(命中:{hit_ord})", not hit_ord)

    # 两名在场角色的立绘都被引用,且文本里以语义名点名
    for label in ("爱丽丝立绘", "优香立绘"):
        in_manifest = any(r.semantic_name == label for r in draft.reference_manifest)
        in_text = label in text
        check(f"「{label}」被引用(清单={in_manifest} 文本点名={in_text})", in_manifest and in_text)

    # 清单每项 source/asset_id/purpose 完整,且 asset_id 对得上库
    id_by_label = {a.label: a.id for a in assets}
    manifest_ok = True
    for r in draft.reference_manifest:
        if r.source == "reference_asset":
            if r.asset_id != id_by_label.get(r.semantic_name):
                manifest_ok = False
        if not r.purpose.strip():
            manifest_ok = False
    check("清单每项 source/asset_id 正确且 purpose 非空", manifest_ok)

    # 图文指代对应:清单里的语义名都在文本中出现
    correspond = all(n in text for n in names)
    check(f"清单语义名都在文本中出现(图文对应)", correspond)

    # 画风融入而非成段搬运:允许借用画种术语,禁止整段照抄条目解释
    span = longest_style_copy_span(text)
    check(
        f"画风融入而非成段搬运(屏蔽画种术语后最长重合段={span!r} len={len(span)}<{STYLE_COPY_MIN_SPAN})",
        len(span) < STYLE_COPY_MIN_SPAN,
    )

    print("\n(画风是否真正被『消化进画面』、用途说明是否贴切,请人工评判上面的 prompt_text。)")


if __name__ == "__main__":
    asyncio.run(main())
