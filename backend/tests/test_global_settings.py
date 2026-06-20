"""全局设置(全站单例 · 接入点供应商配置):加密往返 + 接入点解析(本站/自定义)+ 存取掩码 + HTTP 壳。

不打真 LLM:只验证 base_url / api_key 解析与落库加密。覆盖表是模块级状态,每个用例末尾复位避免泄漏。
"""

import httpx
import pytest

from app.db.session import create_all, make_engine, make_session_factory
from app.llm import endpoints
from app.llm.registry import resolve_chat


def _reset_overrides():
    endpoints.set_overrides({})


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
def test_resolve_endpoint_site_uses_env(monkeypatch):
    monkeypatch.setattr("app.config.settings.google_api_key", "sk-env-google", raising=False)
    _reset_overrides()
    try:
        base_url, key = endpoints.resolve_endpoint("google_text")
        assert key == "sk-env-google"
        assert base_url.endswith("/v1beta/openai/")
        assert endpoints.endpoint_mode("google_text") == "site"
    finally:
        _reset_overrides()


def test_resolve_endpoint_custom_overrides_env(monkeypatch):
    monkeypatch.setattr("app.config.settings.google_api_key", "sk-env-google", raising=False)
    try:
        endpoints.set_overrides(
            {"google_text": endpoints.Override(base_url="https://my.gw/v1/", api_key="sk-custom")}
        )
        base_url, key = endpoints.resolve_endpoint("google_text")
        assert (base_url, key) == ("https://my.gw/v1/", "sk-custom")
        assert endpoints.endpoint_mode("google_text") == "custom"
        # registry 的 client 取自定义 base_url + key
        client, model = resolve_chat("gemini-3.5-flash")
        assert str(client.base_url).startswith("https://my.gw/v1")
        assert client.api_key == "sk-custom"
    finally:
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
            # 即时生效:内存覆盖表已更新
            assert endpoints.resolve_endpoint("openai") == ("https://gw.example/v1", "sk-USER-9999")

            # 改回本站点服务 → 清掉自定义
            await update_app_settings(s, {"openai": {"mode": "site"}})
            assert endpoints.endpoint_mode("openai") == "site"

        # 重新从库载入(模拟重启)仍能还原 custom
        async with Session() as s:
            await update_app_settings(
                s, {"zai": {"mode": "custom", "base_url": "https://z.gw/v1", "api_key": "sk-Z"}}
            )
        _reset_overrides()
        async with Session() as s:
            from app.global_settings_store import load_overrides_into_memory

            await load_overrides_into_memory(s)
        assert endpoints.resolve_endpoint("zai") == ("https://z.gw/v1", "sk-Z")
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
                    s, {"openai": {"mode": "custom", "base_url": "u", "api_key": "k"}}
                )
        # 有 secret 但首次 custom 不给 key → 报错
        monkeypatch.setattr("app.config.settings.app_secret", "sec", raising=False)
        async with Session() as s:
            with pytest.raises(GlobalSettingsError):
                await update_app_settings(s, {"openai": {"mode": "custom", "base_url": "u"}})
            # 未知接入点 → 报错
            with pytest.raises(GlobalSettingsError):
                await update_app_settings(s, {"nope": {"mode": "site"}})
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

            # 只改 URL、不重填 key → 保留旧 key
            await c.put(
                "/global-settings",
                json={"endpoints": {"google_image": {"mode": "custom", "base_url": "https://img2.gw/"}}},
            )
            assert endpoints.resolve_endpoint("google_image") == ("https://img2.gw/", "sk-IMG-7777")

            # 改回本站
            await c.put("/global-settings", json={"endpoints": {"google_image": {"mode": "site"}}})
            assert endpoints.endpoint_mode("google_image") == "site"
    finally:
        _reset_overrides()
        await engine.dispose()
