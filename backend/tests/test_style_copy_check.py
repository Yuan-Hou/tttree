"""精确化『不照抄』核查:允许借用画种术语,禁止成段搬运条目解释。
用 M3-B 已被认可的真实稿件做回归基线(确定性,不调用 API)。"""

from app.agents.illustrator import STYLE_COPY_MIN_SPAN, longest_style_copy_span

# M3-B 中用户已认可的真实 prompt_text(以画种术语开头,其余为消化后的画面描述)
APPROVED_TEXT = (
    "氛围写实的数字概念插画。废弃的旧教室，黄昏最后一缕暖金夕照从破碎的窗户斜射进来，"
    "光柱中浮动着尘埃。翻倒的课桌椅在昏暗中投下凌乱的阴影，积灰的黑板半隐于暗处。"
    "教室中央，两名少女正面对峙，空气紧绷。爱丽丝立绘提供爱丽丝的完整形象：深蓝渐变长发，"
    "白黑蓝配色的学生制服外套，背着巨大的白色科幻武器，神情认真而执拗。优香立绘提供优香的"
    "完整形象：深紫色长发双马尾，黑色西装式制服与白衬衫、蓝色领带，双臂抱胸，眉头紧蹙，"
    "冷静却带着不满。构图采用略低的第二人称视角，前景有倾覆的课桌，中景为对峙的两人，"
    "背景是破窗与夕照。光线从左侧破窗打入，勾勒出角色的轮廓，在她们身上形成强烈的明暗分割，"
    "地面投下长长的影子。色彩以低饱和暖金与暗蓝紫形成冷暖对比，整体呈电影感色调。"
    "木桌的粗糙纹理、玻璃的裂痕、空中飘浮的灰尘均刻画入微，营造出凝重而富有戏剧性的瞬间。"
)

# 故意成段搬运画风圣经「光影」条目解释的反例
COPIED_TEXT = "一间旧屋。强调戏剧性的自然光与环境光。屋里站着一个人。"


def test_medium_terms_allowed_for_approved_draft():
    span = longest_style_copy_span(APPROVED_TEXT)
    # 借用「数字概念插画/氛围写实」被屏蔽后,与画风圣经无成段逐字重合
    assert len(span) < STYLE_COPY_MIN_SPAN, f"approved 稿不应判为照抄,但最长重合段为 {span!r}"
    print(f"\n[allowed] 认可稿最长重合段={span!r}(len={len(span)} < {STYLE_COPY_MIN_SPAN}),判为合规。")


def test_verbatim_entry_explanation_flagged():
    span = longest_style_copy_span(COPIED_TEXT)
    assert len(span) >= STYLE_COPY_MIN_SPAN, f"成段搬运应被判照抄,但最长重合段仅 {span!r}"
    assert "强调戏剧性的自然光与环境光" in span
    print(f"\n[forbidden] 成段搬运被判照抄,最长重合段={span!r}(len={len(span)})。")
