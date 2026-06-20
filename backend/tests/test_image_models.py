"""绘图模型切换(image_model 子步):每故事覆盖解析 + 校验 + 随 fork 复制 + 执行层两条出图 API 分流。

出图不打真 API:OpenAI 路径桩掉 client.images;Gemini 路径桩掉 httpx.AsyncClient.post,验证请求里
带 responseModalities=IMAGE 与参考图 inlineData,且响应里的 base64 图被落盘。
"""

import base64

import httpx
import pytest

from app.db.session import create_all, make_engine, make_session_factory
from app.imaging.executor import ExecResult, ImageGenError, execute_image
from app.stories.settings_store import (
    get_or_create_settings,
    resolve_image_model,
    settings_to_dict,
    update_settings,
)
from app.stories.store import create_story, fork_story

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nFAKE").decode()


async def _session(tmp_path, name="im.db"):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    await create_all(engine)
    return make_session_factory(engine), engine


# ── 每故事覆盖解析 + 校验 ─────────────────────────────────────
async def test_resolve_and_update_image_model(tmp_path):
    Session, engine = await _session(tmp_path)
    try:
        async with Session() as s:
            sid = (await create_story(s, title="T")).id
            st = await get_or_create_settings(s, sid)
            assert resolve_image_model(st) == "gpt-image-2"  # 默认
            assert settings_to_dict(st)["image_model"] == ""
            assert settings_to_dict(st)["image_model_effective"] == "gpt-image-2"

            st = await update_settings(s, sid, image_model="gemini-3.1-flash-image")
            assert resolve_image_model(st) == "gemini-3.1-flash-image"

            # 空串 → 回退默认
            st = await update_settings(s, sid, image_model="")
            assert resolve_image_model(st) == "gpt-image-2"

            # 未知绘图模型 → 拒绝
            with pytest.raises(ValueError):
                await update_settings(s, sid, image_model="no-such-image-model")
    finally:
        await engine.dispose()


async def test_fork_copies_image_model(tmp_path):
    Session, engine = await _session(tmp_path)
    try:
        async with Session() as s:
            sid = (await create_story(s, title="T")).id
            await update_settings(s, sid, image_model="gemini-3.1-flash-image")
            new_id = (await fork_story(s, sid)).id
            st2 = await get_or_create_settings(s, new_id)
            assert st2.image_model == "gemini-3.1-flash-image"
    finally:
        await engine.dispose()


# ── 执行层:OpenAI 路径 ───────────────────────────────────────
async def test_execute_openai_default(tmp_path, monkeypatch):
    class _Resp:
        data = [type("D", (), {"b64_json": _PNG})()]

    class _Images:
        async def generate(self, **kw):
            assert kw["model"]  # 用 gpt-image-2 的快照名
            return _Resp()

        async def edit(self, **kw):
            return _Resp()

    monkeypatch.setattr(
        "app.imaging.executor.get_openai_client", lambda: type("C", (), {"images": _Images()})()
    )
    res = await execute_image(final_prompt="a cat", scene_slug="s", base_dir=tmp_path)
    assert isinstance(res, ExecResult) and res.api_call == "generate"
    assert (tmp_path / res.output_path).read_bytes()  # 落盘了


# ── 执行层:Gemini 路径(文生图 + 参考图) ─────────────────────
async def test_execute_gemini_text_to_image(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.google_api_key", "sk-g", raising=False)
    from app.llm import endpoints

    endpoints.set_overrides({})
    captured = {}

    class _R:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [{"inlineData": {"data": _PNG}}]}}]}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    res = await execute_image(
        final_prompt="seaside city", scene_slug="sc", base_dir=tmp_path,
        image_model="gemini-3.1-flash-image",
    )
    assert res.api_call == "generate"
    assert (tmp_path / res.output_path).read_bytes()
    # 请求形状:走 generateContent,带 IMAGE 模态,鉴权用 x-goog-api-key
    assert ":generateContent" in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "sk-g"
    assert captured["json"]["generationConfig"]["responseModalities"] == ["IMAGE"]


async def test_execute_gemini_with_reference_images(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.google_api_key", "sk-g", raising=False)
    from app.llm import endpoints

    endpoints.set_overrides({})
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\nREF")
    captured = {}

    class _R:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [{"inlineData": {"data": _PNG}}]}}]}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    res = await execute_image(
        final_prompt="variant", scene_slug="sc", base_dir=tmp_path,
        ref_files=[ref], image_model="gemini-3.1-flash-image",
    )
    assert res.api_call == "edit"  # 有参考图
    parts = captured["json"]["contents"][0]["parts"]
    # 第一块是文本,其后是参考图 inlineData
    assert parts[0]["text"] == "variant"
    inline = [p for p in parts if "inlineData" in p]
    assert len(inline) == 1 and inline[0]["inlineData"]["data"]


async def test_execute_gemini_error_on_no_image(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.google_api_key", "sk-g", raising=False)
    from app.llm import endpoints

    endpoints.set_overrides({})

    class _R:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "blocked"}]}}]}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    with pytest.raises(ImageGenError):
        await execute_image(
            final_prompt="x", scene_slug="sc", base_dir=tmp_path,
            image_model="gemini-3.1-flash-image",
        )
