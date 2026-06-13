"""人在回路确认门的结构性保证(离线,不调 LLM/DB/gpt-image-2):
任何 confirm(=下游真出图花钱)都必须由用户键入 y 触发,无旁路;支持反复编辑;逐张一次决策。"""

from app.imaging.draw_service import DraftBundle, confirm_loop
from app.imaging.executor import ResolvedRefs
from app.models.schemas import IllustratorDraft


def _bundle() -> DraftBundle:
    draft = IllustratorDraft(kind="new_scene", prompt_text="原稿", reference_manifest=[])
    return DraftBundle(scene_slug="s", draft=draft, resolved=ResolvedRefs(), history=[])


def _driver(answers):
    q = list(answers)
    def input_fn(_prompt: str = "") -> str:
        assert q, "确认门索要了多于预期的输入——可能存在未预期的循环"
        return q.pop(0)
    return input_fn


def run(answers):
    return confirm_loop(_bundle(), "原稿", input_fn=_driver(answers), print_fn=lambda *_: None)


def test_y_confirms():
    assert run(["y"]) == ("confirm", "原稿")


def test_s_skips_never_confirm():
    assert run(["s"]) == ("skip", "原稿")


def test_r_reuse_never_confirm():
    assert run(["r"]) == ("reuse", "原稿")


def test_edit_then_confirm_carries_edited_prompt():
    assert run(["e", "改后的稿", "y"]) == ("confirm", "改后的稿")


def test_repeated_edit_keeps_last_then_confirm():
    assert run(["e", "一稿", "e", "二稿", "y"]) == ("confirm", "二稿")


def test_empty_edit_keeps_original():
    assert run(["e", "", "y"]) == ("confirm", "原稿")


def test_unrecognized_input_does_not_pass():
    # 乱按("x"、空行、回车)都不放行,只有最终的 y 才确认
    assert run(["x", "", "yes", "确定", "y"]) == ("confirm", "原稿")


def test_confirm_requires_literal_y():
    # 在出现 y 之前的一切非 {y,r,s} 输入都不会返回 confirm/reuse/skip
    import pytest

    # 只喂非决策输入 → 确认门会一直索要输入,driver 在耗尽时断言失败(证明它不会自行放行)
    with pytest.raises(AssertionError):
        run(["x", "n", "go"])
