"""故事内模型设置(子步一):接入层解析 + 每 agent 选模型 + 随 fork/delete 连带 + 默认行为。

不打真 LLM:turn 用例把 turn_router 里的三个 agent monkeypatch 成确定性桩,捕获各自收到的
model,断言「Writer 用 gpt-5.5、其余 deepseek」;Director 桩返回 JSON 经 reduce 正常落库,
证明切模型后 JSON 路径仍可靠。
"""

import json

import httpx
import pytest
from sqlalchemy import select

from app.db.models import StorySettings, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.llm.registry import DEFAULT_MODEL_ID, resolve_chat
from app.models.schemas import DirectorOutput
from app.stories.settings_store import (
    get_or_create_settings,
    resolve_agent_model,
    settings_to_dict,
    update_settings,
)
from app.stories.store import create_story, delete_story, fork_story


def _bb():
    return {"story_meta": {"title": "T", "current_scene": "", "latest_beat": ""},
            "scenes": {}, "characters": {}, "items": {}, "notes": []}


# ── 接入层:解析 ──────────────────────────────────────────────
def test_resolve_chat_maps_provider_and_model(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)

    ds_client, ds_model = resolve_chat("deepseek-v4-pro")
    assert ds_model == settings.deepseek_model
    assert "deepseek" in str(ds_client.base_url)

    oa_client, oa_model = resolve_chat("gpt-5.5")
    assert oa_model == "gpt-5.5"
    assert "openai" in str(oa_client.base_url)


def test_resolve_chat_unknown_falls_back_to_default():
    _, model = resolve_chat(None)
    _, model2 = resolve_chat("nonsense-model")
    assert model == model2  # 都回落默认(deepseek),不崩


# ── 设置解析:覆盖 vs 默认 ────────────────────────────────────
async def _setup(tmp_path, name="ms.db"):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    await create_all(engine)
    return make_session_factory(engine), engine


async def test_new_story_defaults_all_deepseek(tmp_path):
    Session, engine = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        st = await get_or_create_settings(s, sid)
        d = settings_to_dict(st)
    assert d["default_model"] == DEFAULT_MODEL_ID
    assert all(v == "" for v in d["overrides"].values())          # 无覆盖
    assert all(v == DEFAULT_MODEL_ID for v in d["effective"].values())  # 实际全 deepseek
    await engine.dispose()


async def test_override_takes_precedence_else_default(tmp_path):
    Session, engine = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        st = await update_settings(s, sid, overrides={"writer": "gpt-5.5"})
    assert resolve_agent_model(st, "writer") == "gpt-5.5"        # 覆盖生效
    assert resolve_agent_model(st, "director_a") == DEFAULT_MODEL_ID  # 其余仍默认
    await engine.dispose()


async def test_update_rejects_unknown_model(tmp_path):
    Session, engine = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        with pytest.raises(ValueError):
            await update_settings(s, sid, default_model="claude-classified")
        with pytest.raises(ValueError):
            await update_settings(s, sid, overrides={"writer": "bogus"})
    await engine.dispose()


# ── 随 fork 复制、随 delete 清理 ─────────────────────────────
async def test_settings_copied_on_fork(tmp_path):
    Session, engine = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        await update_settings(s, sid, default_model="gpt-5.5", overrides={"director_b": "deepseek-v4-pro"})
        new = await fork_story(s, sid)
        copied = await s.get(StorySettings, new.id)
    assert copied is not None
    assert copied.default_model == "gpt-5.5"
    assert copied.director_b_model == "deepseek-v4-pro"
    await engine.dispose()


async def test_settings_cleaned_on_delete(tmp_path):
    Session, engine = await _setup(tmp_path)
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        await get_or_create_settings(s, sid)
        await delete_story(s, sid)
        gone = await s.get(StorySettings, sid)
    assert gone is None
    await engine.dispose()


# ── 端到端:一轮里 Writer 走 gpt-5.5、其余 deepseek;Director JSON 仍正常落库 ──
@pytest.fixture
async def turn_env(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'turn.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.web.turn_router.async_session", Session)

    seen: dict[str, str] = {}

    async def fake_director(*a, model=None, **k):
        seen["director_a"] = model
        return DirectorOutput(situation="s", beat_points=["b"], mood="m", writing_brief="brief")

    async def fake_writer(*a, model=None, **k):
        seen["writer"] = model
        for ch in "一段叙事":
            yield ch

    async def fake_review(*a, model=None, **k):
        seen["director_b"] = model
        return _bb()  # 合法黑板 → reduce 正常落库,证明切模型后 JSON 路径仍可靠

    monkeypatch.setattr("app.web.turn_router.run_director", fake_director)
    monkeypatch.setattr("app.web.turn_router.stream_writer", fake_writer)
    monkeypatch.setattr("app.web.turn_router.run_director_review", fake_review)

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, Session, seen
    await engine.dispose()


async def test_turn_uses_per_agent_models(turn_env):
    c, Session, seen = turn_env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        await update_settings(s, sid, overrides={"writer": "gpt-5.5"})

    r = await c.post(f"/story/{sid}/turn", json={"user_input": "走进门"})
    assert r.status_code == 200
    body = r.text  # 读完短命 SSE → 生成器跑完(含 reduce 落库)
    assert "turn_done" in body

    assert seen == {"director_a": DEFAULT_MODEL_ID, "writer": "gpt-5.5", "director_b": DEFAULT_MODEL_ID}
    # JSON 路径仍可靠:该轮已正常落库
    async with Session() as s:
        n = (await s.execute(select(Turn).where(Turn.story_id == sid))).scalars().all()
    assert len(n) == 1 and n[0].narrative == "一段叙事"


# ── 设置 HTTP 壳 ─────────────────────────────────────────────
async def test_settings_api_roundtrip(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.db.session.async_session", Session)

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        async with Session() as s:
            sid = (await create_story(s, title="T")).id

        g = (await c.get(f"/story/{sid}/settings")).json()
        assert g["default_model"] == DEFAULT_MODEL_ID
        assert any(m["id"] == "gpt-5.5" for m in g["models"])  # 可选模型清单暴露给前端

        p = await c.put(f"/story/{sid}/settings", json={"overrides": {"writer": "gpt-5.5"}})
        assert p.status_code == 200
        assert p.json()["effective"]["writer"] == "gpt-5.5"

        bad = await c.put(f"/story/{sid}/settings", json={"default_model": "nope"})
        assert bad.status_code == 422
    await engine.dispose()
