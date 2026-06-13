"""绘图 Agent(DeepSeek):写「带明确参考图用途说明的提示词稿」。

复用 build_messages 共享前缀(system 文风圣经 + 历史),易变区含 黑板 + 画风圣经 +
参考图库清单。只写稿,不出图、不花钱。
"""

import json
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from app.agents.context import Blackboard, Message, build_messages
from app.agents.loader import load_prompt
from app.config import settings
from app.db.models import ReferenceAsset
from app.llm.deepseek_client import get_client
from app.models.schemas import IllustratorDraft

VISUAL_STYLE_BIBLE = load_prompt("visual_style_bible.md")

# 画种/媒介定性术语:允许直接借用(对图像模型反而有用),不算「照抄」
ALLOWED_STYLE_TERMS = ("数字概念插画", "氛围写实")
# 屏蔽允许术语后,与画风圣经的最长连续公共子串达到此长度即视为「成段搬运条目解释」
STYLE_COPY_MIN_SPAN = 12


def longest_style_copy_span(
    text: str,
    style_bible: str = VISUAL_STYLE_BIBLE,
    allowed_terms: Sequence[str] = ALLOWED_STYLE_TERMS,
) -> str:
    """返回 text 与画风圣经之间最长的连续公共子串(已屏蔽允许的画种术语与换行)。

    用途:区分「借用画种术语」(允许,屏蔽后不会形成长串)与「成段搬运条目解释」
    (禁止,会留下一段较长的逐字重合)。子串不跨越换行/被屏蔽术语。
    """
    sent = "\x00"

    def mask(s: str) -> str:
        for t in allowed_terms:
            s = s.replace(t, sent)
        return s.replace("\n", sent)

    a, b = mask(text), mask(style_bible)
    prev = [0] * (len(b) + 1)
    best_len, best_end = 0, 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ca = a[i - 1]
        if ca != sent:
            for j in range(1, len(b) + 1):
                if ca == b[j - 1]:
                    cur[j] = prev[j - 1] + 1
                    if cur[j] > best_len:
                        best_len, best_end = cur[j], i
        prev = cur
    return a[best_end - best_len : best_end].replace(sent, "")


class IllustratorError(Exception):
    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def render_reference_catalog(
    assets: Sequence[ReferenceAsset],
    history_images: Sequence[dict[str, Any]] = (),
) -> str:
    """渲染参考图库清单给绘图 Agent。

    history_images: [{"semantic_name": "场景名·状态", "image_path": "...", "note": "..."}]
    对 Agent 只暴露语义名 + 用于判断相关性的说明 + 供清单回填的 asset_id/path;不含位置序号。
    """
    lines: list[str] = []
    lines.append("参考图库(用户登记的素材,用其『语义名』指代):")
    if assets:
        for a in assets:
            lines.append(
                f"- 语义名「{a.label}」(类别:{a.category};asset_id={a.id}):{a.description}"
            )
    else:
        lines.append("-(暂无登记的参考素材)")

    lines.append("")
    lines.append("历史生成图(本故事已画过的图,可用于同场景变体的空间/视觉连贯):")
    if history_images:
        for h in history_images:
            note = h.get("note", "")
            lines.append(
                f"- 语义名「{h['semantic_name']}」(image_path={h['image_path']}):{note}"
            )
    else:
        lines.append("-(暂无历史生成图)")

    return "\n".join(lines)


async def run_illustrator(
    *,
    history: list[Message],
    blackboard: Blackboard,
    draw_request: str,
    reference_catalog: str,
    visual_style: str = VISUAL_STYLE_BIBLE,
) -> IllustratorDraft:
    client = get_client()
    messages = build_messages(
        "illustrator",
        history=history,
        blackboard=blackboard,
        user_action=draw_request,
        visual_style=visual_style,
        reference_catalog=reference_catalog,
    )

    response = await client.chat.completions.create(
        model=settings.deepseek_model,
        messages=messages,
        temperature=0.4,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IllustratorError(f"JSON 解析失败: {exc}", raw) from exc
    try:
        return IllustratorDraft.model_validate(data)
    except ValidationError as exc:
        raise IllustratorError(f"Schema 校验失败: {exc}", raw) from exc
