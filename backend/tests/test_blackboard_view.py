"""黑板「视图」:发给各 agent 的黑板剥离系统管理字段(image_paths / origin_turn)。

这些字段 agent 既不需要也不被信任——image_paths 由出图流程维护、origin_turn 由 reducer 维护,
B 的回显从不被采信(reducer 落库时按 slug 权威加回)。统一在 _render_blackboard 里剥离,
四个 agent 收到的黑板视图逐字节一致;持久黑板本身不受影响。
"""

import copy

from app.agents.context import _blackboard_view, _render_blackboard, build_messages

_BB = {
    "story_meta": {"current_scene": "lab", "latest_beat": "开端"},
    "scenes": {
        "lab": {
            "name": "实验室",
            "state": "灯还亮着",
            "connections": ["hall"],
            "base_prompt": "冷白调实验室",
            "visual_anchors": ["试管架"],
            "image_paths": ["storage/images/lab_1.png", "storage/images/lab_2.png"],
            "origin_turn": 3,
        },
    },
    "characters": {}, "items": {}, "notes": [],
}


def test_render_blackboard_strips_system_fields_keeps_world_state():
    rendered = _render_blackboard(_BB)
    # 系统字段被剥离(键与值都不出现)
    assert "image_paths" not in rendered
    assert "origin_turn" not in rendered
    assert "lab_1.png" not in rendered
    # 世界状态字段保留
    assert "实验室" in rendered and "灯还亮着" in rendered and "hall" in rendered
    assert "冷白调实验室" in rendered and "试管架" in rendered


def test_view_does_not_mutate_persistent_blackboard():
    original = copy.deepcopy(_BB)
    _blackboard_view(_BB)
    _render_blackboard(_BB)
    assert _BB == original  # 只影响渲染,不动持久黑板
    assert _BB["scenes"]["lab"]["image_paths"] == ["storage/images/lab_1.png", "storage/images/lab_2.png"]
    assert _BB["scenes"]["lab"]["origin_turn"] == 3


def test_all_agents_embed_identical_stripped_blackboard():
    """同回合四个 agent 收到逐字节相同的黑板视图。"""
    rendered = _render_blackboard(_BB)
    common = dict(history=[], blackboard=_BB, user_action="看一看")
    a = build_messages("director", **common)
    w = build_messages("writer", writing_brief="wb", **common)
    b = build_messages("director_review", narrative="一段叙事", **common)
    ill = build_messages("illustrator", visual_style="vs", reference_catalog="rc", **common)
    for msgs in (a, w, b, ill):
        assert rendered in msgs[-1]["content"]


def test_view_tolerates_missing_or_malformed_scenes():
    assert _blackboard_view({}) == {}  # 无 scenes 键:原样返回,不崩
    assert _blackboard_view({"scenes": "oops"}) == {"scenes": "oops"}  # scenes 非 dict:原样返回
    weird = {"scenes": {"x": "not-a-dict"}}
    assert _blackboard_view(weird)["scenes"]["x"] == "not-a-dict"  # 场景值非 dict:原样保留
