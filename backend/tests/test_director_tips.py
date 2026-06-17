"""Director-A 的 tips:取代死字段 choices,作为「A → 下游」传递本轮相关设定的唯一通道。

只有 A 能看知识库;tips 由 A 摘出,注入 Writer / Director-B / 绘图写稿 的易变区尾部(不进 A 自己、
不进 system / history 前缀 → 不击穿缓存)。空 tips 不渲染任何块。
(Options 角色的 tips 注入随 Options agent 一并在 test_options 里覆盖。)
"""

from app.agents.context import _render_tips, build_messages
from app.models.schemas import DirectorOutput

_BB = {"story_meta": {"current_scene": "lab"}, "scenes": {"lab": {"name": "实验室", "state": "亮着"}}}
_TIPS = ["白子说话爱用敬语", "灯塔矗立在悬崖边缘"]


def test_directoroutput_has_tips_not_choices():
    d = DirectorOutput(situation="s", writing_brief="wb")
    assert d.tips == []  # 默认空
    assert not hasattr(d, "choices")  # 死字段已删
    d2 = DirectorOutput(situation="s", writing_brief="wb", tips=_TIPS)
    assert "白子说话爱用敬语" in d2.model_dump_json()  # 存档(director_a_json)带 tips


def test_empty_tips_renders_nothing():
    assert _render_tips([]) == ""
    assert _render_tips(None) == ""
    assert _render_tips(["  ", ""]) == ""  # 全空白条目也不渲染


def test_tips_injected_into_downstream_volatile_tail():
    common = dict(history=[], blackboard=_BB, user_action="走进去", tips=_TIPS)
    writer = build_messages("writer", writing_brief="wb", **common)
    b = build_messages("director_review", narrative="一段叙事", **common)
    ill = build_messages("illustrator", visual_style="vs", reference_catalog="rc", **common)
    for msgs in (writer, b, ill):
        tail = msgs[-1]["content"]  # 易变区(最后一条 user)
        assert "本轮设定提示" in tail and "敬语" in tail
        # tips 在尾部:排在黑板之后
        assert tail.index("本轮设定提示") > tail.index("当前黑板")
        # 绝不进 system / history 前缀
        assert all("敬语" not in m["content"] for m in msgs[:-1])


def test_tips_never_reaches_director_a():
    # A 是产出方:即便误传 tips,build_messages 也结构性地不给 director 渲染
    a = build_messages("director", history=[], blackboard=_BB, user_action="走进去", knowledge="K", tips=_TIPS)
    assert all("本轮设定提示" not in m["content"] for m in a)


def test_director_prefix_unchanged_by_tips_arg():
    # 缓存前缀(system + history)不随 tips 改变 —— 只动易变 tail
    base = build_messages("writer", history=[], blackboard=_BB, user_action="x", writing_brief="wb")
    withtips = build_messages("writer", history=[], blackboard=_BB, user_action="x", writing_brief="wb", tips=_TIPS)
    assert base[0] == withtips[0]  # system 逐字节一致
    assert base[-1]["content"] in withtips[-1]["content"]  # 原 tail 是新 tail 的前缀(只在末尾追加)
