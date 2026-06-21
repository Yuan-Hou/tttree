"""全局设置(全站单例 · 接入点供应商配置):加密往返 + 接入点解析(本站/自定义)+ 存取掩码 + HTTP 壳。

不打真 LLM:只验证 base_url / api_key 解析与落库加密。覆盖表是模块级状态,每个用例末尾复位避免泄漏。
"""

import asyncio

import httpx
import pytest

from app.auth.context import current_uid
from app.db.session import create_all, make_engine, make_session_factory
from app.llm import endpoints
from app.llm.registry import resolve_chat


def _reset_overrides():
    endpoints.clear_all_overrides()


# ── 加密 ────────────────────────────────────────────────────
def test_crypto_roundtrip(monkeypatch):
    from app import crypto

    monkeypatch.setattr("app.config.settings.app_secret", "test-secret-123", raising=False)
    assert crypto.is_available()
    enc = crypto.encrypt("sk-secret-value")
    assert enc.startswith("f1:")
    assert "sk-secret-value" not in enc  # 密文不含明文
    assert crypto.decrypt(enc) == "sk-secret-value"


def test_crypto_unavailable_without_secret(monkeypatch):
    from app import crypto

    monkeypatch.setattr("app.config.settings.app_secret", None, raising=False)
    assert not crypto.is_available()
    with pytest.raises(crypto.CryptoUnavailable):
        crypto.encrypt("x")


def test_crypto_wrong_secret_fails(monkeypatch):
    from app import crypto

    monkeypatch.setattr("app.config.settings.app_secret", "secret-A", raising=False)
    enc = crypto.encrypt("sk-abc")
    monkeypatch.setattr("app.config.settings.app_secret", "secret-B", raising=False)
    with pytest.raises(crypto.CryptoUnavailable):
        crypto.decrypt(enc)


# ── 接入点解析 ───────────────────────────────────────────────
def test_resolve_endpoint_site_uses_new_api(monkeypatch):
    """本站点服务 → 经 new-api 网关 + 该用户 token;无 token → key=None(不回落 .env)。"""
    monkeypatch.setattr("app.config.settings.new_api_base_url", "https://gw.test", raising=False)
    _reset_overrides()
    try:
        base, key = endpoints.resolve_endpoint("google_text", user_id="9")  # 9 号尚无 token
        assert base == "https://gw.test/v1" and key is None
        assert endpoints.endpoint_mode("google_text", user_id="9") == "site"
        # 有 token:各接入点按其路径段拼到 new-api host
        endpoints.set_user_site_key("9", "sk-u9")
        assert endpoints.resolve_endpoint("google_text", user_id="9") == ("https://gw.test/v1", "sk-u9")
        assert endpoints.resolve_endpoint("anthropic", user_id="9") == ("https://gw.test", "sk-u9")
        assert endpoints.resolve_endpoint("google_image", user_id="9") == ("https://gw.test/v1beta/", "sk-u9")
    finally:
        endpoints.set_user_site_key("9", "")
        _reset_overrides()


def test_resolve_endpoint_custom_overrides_env():
    tok = current_uid.set("1")  # 模拟「1 号用户的请求/作业」上下文
    try:
        endpoints.set_user_overrides(
            "1", {"google_text": endpoints.Override(base_url="https://my.gw/v1/", api_key="sk-custom")}
        )
        # 显式 user_id 与 contextvar 两条路径都取到自定义
        assert endpoints.resolve_endpoint("google_text", user_id="1") == ("https://my.gw/v1/", "sk-custom")
        assert endpoints.resolve_endpoint("google_text") == ("https://my.gw/v1/", "sk-custom")
        assert endpoints.endpoint_mode("google_text") == "custom"
        # registry 的 client 经 contextvar 取自定义 base_url + key
        client, model = resolve_chat("gemini-3.5-flash")
        assert str(client.base_url).startswith("https://my.gw/v1")
        assert client.api_key == "sk-custom"
    finally:
        current_uid.reset(tok)
        _reset_overrides()


async def test_resolve_propagates_into_create_task():
    """核心不变量:在 current_uid 已设的上下文里 create_task,子任务继承该 uid → 深处 resolve_endpoint
    (不显式传 uid)取到该用户的覆盖。这正是回合/绘图后台作业(start_*_job → create_task)的凭证穿透路径。"""
    endpoints.set_user_overrides(
        "1", {"openai": endpoints.Override(base_url="https://u1/v1", api_key="sk-u1")}
    )
    tok = current_uid.set("1")
    try:
        out: dict = {}

        async def _job() -> None:
            out["res"] = endpoints.resolve_endpoint("openai")  # 无显式 uid,靠继承的 ctx

        await asyncio.create_task(_job())
        assert out["res"] == ("https://u1/v1", "sk-u1")
    finally:
        current_uid.reset(tok)
        _reset_overrides()


def test_overrides_are_per_user(monkeypatch):
    monkeypatch.setattr("app.config.settings.new_api_base_url", "https://gw.test", raising=False)
    try:
        endpoints.set_user_overrides(
            "1", {"openai": endpoints.Override(base_url="https://u1.gw/v1", api_key="sk-u1")}
        )
        endpoints.set_user_site_key("2", "sk-u2-site")
        # 1 号:自定义;2 号:本站点服务(new-api + 自己的 token);9 号:无 token → key=None
        assert endpoints.resolve_endpoint("openai", user_id="1") == ("https://u1.gw/v1", "sk-u1")
        assert endpoints.resolve_endpoint("openai", user_id="2") == ("https://gw.test/v1", "sk-u2-site")
        assert endpoints.endpoint_mode("openai", user_id="2") == "site"
        assert endpoints.resolve_endpoint("openai", user_id="9")[1] is None
    finally:
        endpoints.set_user_site_key("2", "")
        _reset_overrides()


# ── store:落库加密 + 公开载荷掩码 + 即时生效 ──────────────────
async def _session(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'gs.db'}")
    await create_all(engine)
    return make_session_factory(engine), engine


async def test_store_custom_encrypts_masks_and_applies(tmp_path, monkeypatch):
    from app.global_settings_store import get_app_settings, public_payload, update_app_settings

    monkeypatch.setattr("app.config.settings.app_secret", "store-secret", raising=False)
    Session, engine = await _session(tmp_path)
    try:
        async with Session() as s:
            row = await update_app_settings(
                s,
                "1",
                {"openai": {"mode": "custom", "base_url": "https://gw.example/v1", "api_key": "sk-USER-9999"}},
            )
            # 落库为密文,不含明文
            assert "sk-USER-9999" not in row.endpoints_json
            assert "f1:" in row.endpoints_json
            # 公开载荷:不回传明文/密文,只给掩码 + key_set
            payload = public_payload(row)
            oa = next(e for e in payload["endpoints"] if e["id"] == "openai")
            assert oa["mode"] == "custom" and oa["key_set"] is True
            assert oa["base_url"] == "https://gw.example/v1"
            assert "sk-USER-9999" not in str(payload) and "f1:" not in str(payload)
            assert oa["key_masked"].endswith("9999")  # 只露尾 4 位
            # 即时生效:1 号用户的内存覆盖表已更新
            assert endpoints.resolve_endpoint("openai", user_id="1") == ("https://gw.example/v1", "sk-USER-9999")

            # 改回本站点服务 → 清掉自定义
            await update_app_settings(s, "1", {"openai": {"mode": "site"}})
            assert endpoints.endpoint_mode("openai", user_id="1") == "site"

        # 重新从库载入(模拟重启)仍能还原 custom
        async with Session() as s:
            await update_app_settings(
                s, "1", {"zai": {"mode": "custom", "base_url": "https://z.gw/v1", "api_key": "sk-Z"}}
            )
        _reset_overrides()
        async with Session() as s:
            from app.global_settings_store import load_overrides_into_memory

            await load_overrides_into_memory(s)
        assert endpoints.resolve_endpoint("zai", user_id="1") == ("https://z.gw/v1", "sk-Z")
    finally:
        _reset_overrides()
        await engine.dispose()


async def test_store_custom_requires_key_and_secret(tmp_path, monkeypatch):
    from app.global_settings_store import GlobalSettingsError, update_app_settings

    Session, engine = await _session(tmp_path)
    try:
        # APP_SECRET 未配置 → 不能存自填 key
        monkeypatch.setattr("app.config.settings.app_secret", None, raising=False)
        async with Session() as s:
            with pytest.raises(GlobalSettingsError):
                await update_app_settings(
                    s, "1", {"openai": {"mode": "custom", "base_url": "u", "api_key": "k"}}
                )
        # 有 secret 但首次 custom 不给 key → 报错
        monkeypatch.setattr("app.config.settings.app_secret", "sec", raising=False)
        async with Session() as s:
            with pytest.raises(GlobalSettingsError):
                await update_app_settings(s, "1", {"openai": {"mode": "custom", "base_url": "u"}})
            # 未知接入点 → 报错
            with pytest.raises(GlobalSettingsError):
                await update_app_settings(s, "1", {"nope": {"mode": "site"}})
    finally:
        _reset_overrides()
        await engine.dispose()


# ── HTTP 壳 ──────────────────────────────────────────────────
async def _client(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'gs_api.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.db.session.async_session", Session)
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t"), engine


async def test_global_settings_api_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.app_secret", "api-secret", raising=False)
    c, engine = await _client(tmp_path, monkeypatch)
    try:
        async with c:
            # 默认全部本站点服务,6 个接入点
            g = (await c.get("/global-settings")).json()
            assert len(g["endpoints"]) == 6
            assert all(e["mode"] == "site" for e in g["endpoints"])
            assert {e["id"] for e in g["endpoints"]} == {
                "deepseek", "openai", "zai", "anthropic", "google_text", "google_image"
            }

            # 设 google_image 为自定义
            p = await c.put(
                "/global-settings",
                json={"endpoints": {"google_image": {
                    "mode": "custom", "base_url": "https://img.gw/", "api_key": "sk-IMG-7777"
                }}},
            )
            assert p.status_code == 200
            gi = next(e for e in p.json()["endpoints"] if e["id"] == "google_image")
            assert gi["mode"] == "custom" and gi["key_set"] is True
            assert gi["key_masked"].endswith("7777")
            # 响应不含明文
            assert "sk-IMG-7777" not in (await c.get("/global-settings")).text

            # 只改 URL、不重填 key → 保留旧 key(HTTP 经 conftest 默认以 1 号用户登录)
            await c.put(
                "/global-settings",
                json={"endpoints": {"google_image": {"mode": "custom", "base_url": "https://img2.gw/"}}},
            )
            assert endpoints.resolve_endpoint("google_image", user_id="1") == ("https://img2.gw/", "sk-IMG-7777")

            # 改回本站
            await c.put("/global-settings", json={"endpoints": {"google_image": {"mode": "site"}}})
            assert endpoints.endpoint_mode("google_image", user_id="1") == "site"
    finally:
        _reset_overrides()
        await engine.dispose()
