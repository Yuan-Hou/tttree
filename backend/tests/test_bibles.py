"""故事自定义圣经(文风 / 画风)+ 预制模板(故事内设置 · bible 子步)。

覆盖:① 模板启动扫描含 default、内容即默认;② resolve 的「空→默认 / 非空→自定义」;
③ build_messages 用每故事 style_bible 作 system 首条、缺省回退默认、且同回合各叙事 agent
逐字节一致(缓存铁律不破);④ illustrator 用每故事 visual_style;⑤ 随 fork 复制、随 delete 清理。
"""

import pytest

from app.agents.bibles import (
    DEFAULT_STYLE_BIBLE,
    DEFAULT_VISUAL_STYLE_BIBLE,
    STYLE_TEMPLATES,
    VISUAL_TEMPLATES,
    resolve_style_bible,
    resolve_visual_style_bible,
)
from app.agents.context import STYLE_BIBLE, build_messages
from app.db.models import StorySettings
from app.db.session import create_all, make_engine, make_session_factory
from app.stories.settings_store import get_or_create_settings, update_bibles
from app.stories.store import create_story, delete_story, fork_story

_BB = {"story_meta": {"current_scene": "", "latest_beat": ""},
       "scenes": {}, "characters": {}, "items": {}, "notes": []}


# ── 模板扫描 ────────────────────────────────────────────────
def test_templates_scanned_with_default_first():
    for templates, default in ((STYLE_TEMPLATES, DEFAULT_STYLE_BIBLE),
                               (VISUAL_TEMPLATES, DEFAULT_VISUAL_STYLE_BIBLE)):
        names = [t["name"] for t in templates]
        assert "default" in names
        assert names[0] == "default"            # default 恒首位
        by_name = {t["name"]: t["content"] for t in templates}
        assert by_name["default"] == default    # default 模板正文即生效默认


# ── resolve:空→默认,非空→自定义 ───────────────────────────
def test_resolve_falls_back_to_default_when_empty():
    assert resolve_style_bible("") == DEFAULT_STYLE_BIBLE
    assert resolve_style_bible(None) == DEFAULT_STYLE_BIBLE
    assert resolve_style_bible("   ") == DEFAULT_STYLE_BIBLE
    assert resolve_visual_style_bible("") == DEFAULT_VISUAL_STYLE_BIBLE


def test_resolve_uses_custom_when_present():
    assert resolve_style_bible("我的专属文风") == "我的专属文风"
    assert resolve_visual_style_bible("我的专属画风") == "我的专属画风"


# ── build_messages:每故事 style_bible 作 system 首条 ────────
def test_custom_style_bible_becomes_system_prefix():
    custom = "【本故事专属文风】只用第二人称。"
    common = dict(history=[], blackboard=_BB, user_action="看一看", style_bible=custom)
    a = build_messages("director", **common)
    w = build_messages("writer", writing_brief="wb", **common)
    b = build_messages("director_review", narrative="叙事", **common)
    o = build_messages("options", narrative="叙事", **common)
    for msgs in (a, w, b, o):
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == custom        # 自定义即 system 首条
    # 缓存铁律:同回合各叙事 agent 的 system 前缀逐字节一致
    assert a[0]["content"] == w[0]["content"] == b[0]["content"] == o[0]["content"]


def test_omitting_style_bible_uses_packaged_default():
    msgs = build_messages("writer", history=[], blackboard=_BB, user_action="x", writing_brief="wb")
    assert msgs[0]["content"] == STYLE_BIBLE == DEFAULT_STYLE_BIBLE


def test_custom_visual_style_injected_for_illustrator():
    vs = "【本故事专属画风】高对比黑白。"
    msgs = build_messages("illustrator", history=[], blackboard=_BB, user_action="画",
                          visual_style=vs, reference_catalog="rc")
    assert vs in msgs[-1]["content"]               # 画风圣经在 illustrator 易变区


# ── 随 fork 复制、随 delete 清理 ────────────────────────────
async def _setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'bibles.db'}")
    await create_all(engine)
    return make_session_factory(engine), engine


async def test_bibles_default_empty_and_update(tmp_path):
    Session, engine = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        st = await get_or_create_settings(s, sid)
        assert st.style_bible == "" and st.visual_style_bible == ""   # 新故事:空=用默认
        st = await update_bibles(s, sid, style_bible="自定文风")
        assert st.style_bible == "自定文风" and st.visual_style_bible == ""  # 只改传入的
        st = await update_bibles(s, sid, visual_style_bible="自定画风")
        assert st.style_bible == "自定文风" and st.visual_style_bible == "自定画风"
        st = await update_bibles(s, sid, style_bible="")              # 空串=清空→回退默认
        assert st.style_bible == ""
    await engine.dispose()


async def test_bibles_copied_on_fork_and_cleaned_on_delete(tmp_path):
    Session, engine = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        await update_bibles(s, sid, style_bible="原文风", visual_style_bible="原画风")
        new = await fork_story(s, sid)
        copied = await s.get(StorySettings, new.id)
        assert copied is not None
        assert copied.style_bible == "原文风" and copied.visual_style_bible == "原画风"  # 随副本复制
        await delete_story(s, sid)
        assert await s.get(StorySettings, sid) is None                # 随 delete 清理
    await engine.dispose()
