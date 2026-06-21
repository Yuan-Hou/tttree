"""Stage A:new-api 建号/取 key 客户端 + 惰性补齐存储。全程 mock httpx,绝不真连 new-api 站点。"""

import pytest

from app.db.session import create_all, make_engine, make_session_factory
from app.newapi import client as nac
from app.newapi.client import NewApiError, ProvisionedAccount, provision


class _Resp:
    def __init__(self, payload=None, status=200, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    """按 (method, path) 路由出 new-api 各步的假响应,并记录每次调用的 headers 供断言。"""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.calls.append(("POST", url, headers or {}, json or {}))
        if url.endswith("/api/user/"):
            return _Resp({"success": True, "message": ""})
        if url.endswith("/api/user/login"):
            return _Resp({"success": True, "data": {"id": 42, "username": (json or {}).get("username")}})
        if url.endswith("/api/token/"):
            return _Resp({"success": True, "message": ""})
        if url.endswith("/api/token/7/key"):
            return _Resp({"success": True, "data": {"key": "RAWKEY48CHARS"}})
        return _Resp({"success": False, "message": f"unexpected POST {url}"}, status=404)

    async def get(self, url, headers=None):
        _FakeClient.calls.append(("GET", url, headers or {}, {}))
        if "/api/token/" in url:
            return _Resp({"success": True, "data": {"items": [
                {"id": 5, "name": "other", "key": "xx"},
                {"id": 7, "name": "vore-1", "key": "ma****ed"},
            ]}})
        return _Resp({"success": False, "message": f"unexpected GET {url}"}, status=404)


def _configure(monkeypatch, client_cls=_FakeClient):
    monkeypatch.setattr("app.config.settings.new_api_base_url", "https://api.test", raising=False)
    monkeypatch.setattr("app.config.settings.new_api_admin_key", "admin-access-token", raising=False)
    monkeypatch.setattr("app.config.settings.new_api_admin_user_id", "1", raising=False)
    monkeypatch.setattr("app.config.settings.site_name", "TT", raising=False)
    monkeypatch.setattr(nac.httpx, "AsyncClient", client_cls)
    client_cls.calls = []


async def test_provision_full_flow(monkeypatch):
    _configure(monkeypatch)
    acc = await provision("1")
    assert isinstance(acc, ProvisionedAccount)
    assert acc.newapi_user_id == 42
    assert acc.token_id == 7
    assert acc.api_key == "sk-RAWKEY48CHARS"  # 'sk-' 前缀
    # 用户名:{规整品牌}_{uid}_{6位随机},≤20;口令 8–20
    assert acc.username.startswith("tt_1_") and len(acc.username) <= 20
    assert len(acc.username.rsplit("_", 1)[-1]) == 6  # 末段是 6 位随机
    assert 8 <= len(acc.password) <= 20

    calls = _FakeClient.calls
    # 建用户用管理员头(Authorization=admin token + New-Api-User=admin id)
    create = next(c for c in calls if c[1].endswith("/api/user/"))
    assert create[2]["Authorization"] == "admin-access-token"
    assert create[2]["New-Api-User"] == "1"
    # 建令牌/列表/取 key 用子用户头(New-Api-User=42),不带 admin Authorization
    tok = next(c for c in calls if c[0] == "POST" and c[1].endswith("/api/token/"))
    assert tok[2]["New-Api-User"] == "42" and "Authorization" not in tok[2]


async def test_provision_not_configured(monkeypatch):
    monkeypatch.setattr("app.config.settings.new_api_admin_key", None, raising=False)
    with pytest.raises(NewApiError):
        await provision("1")


async def test_provision_propagates_failure(monkeypatch):
    class _Fail(_FakeClient):
        async def post(self, url, headers=None, json=None):
            if url.endswith("/api/user/"):
                return _Resp({"success": False, "message": "用户名已存在"})
            return await super().post(url, headers=headers, json=json)

    _configure(monkeypatch, _Fail)
    with pytest.raises(NewApiError):
        await provision("1")


async def test_ensure_account_idempotent(tmp_path, monkeypatch):
    from app.newapi.store import ensure_account, get_account

    _configure(monkeypatch)
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'na.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    try:
        async with Session() as s:
            a1 = await ensure_account(s, "1")
            assert a1 is not None and a1.api_key == "sk-RAWKEY48CHARS"
        # 第二次:已存在 → 不再建号(不新增 httpx 调用)
        _FakeClient.calls = []
        async with Session() as s:
            a2 = await ensure_account(s, "1")
            assert a2 is not None and a2.newapi_user_id == 42
            assert _FakeClient.calls == []  # 没有再打 new-api
            assert await get_account(s, "1") is not None
    finally:
        await engine.dispose()


async def test_get_user_quota(monkeypatch):
    from app.newapi.client import get_user_quota

    class _QClient(_FakeClient):
        async def get(self, url, headers=None):
            _FakeClient.calls.append(("GET", url, headers or {}, {}))
            if url.endswith("/api/user/42"):
                return _Resp({"success": True, "data": {"quota": 250000, "used_quota": 1000}})
            return await super().get(url, headers=headers)

    _configure(monkeypatch, _QClient)
    q = await get_user_quota(42)
    assert q == {"quota": 250000, "used_quota": 1000}
    # 用管理员头查
    g = next(c for c in _FakeClient.calls if c[0] == "GET" and c[1].endswith("/api/user/42"))
    assert g[2]["Authorization"] == "admin-access-token" and g[2]["New-Api-User"] == "1"


async def test_balance_endpoint_no_account(tmp_path, monkeypatch):
    """无 new-api 账号 → ready=False(conftest 默认以 1 号用户登录,DB 里没有其账号行)。"""
    import httpx as _httpx

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'bal.db'}")
    await create_all(engine)
    monkeypatch.setattr("app.db.session.async_session", make_session_factory(engine))
    from app.main import app

    async with _httpx.AsyncClient(transport=_httpx.ASGITransport(app=app), base_url="http://t") as c:
        b = (await c.get("/auth/balance")).json()
        assert b["ready"] is False and b["error"]
    await engine.dispose()


async def test_ensure_account_failure_returns_none(tmp_path, monkeypatch):
    from app.newapi.store import ensure_account

    class _Fail(_FakeClient):
        async def post(self, url, headers=None, json=None):
            if url.endswith("/api/user/login"):
                return _Resp({"success": False, "message": "宕机"})
            return await super().post(url, headers=headers, json=json)

    _configure(monkeypatch, _Fail)
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'na2.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    try:
        async with Session() as s:
            assert await ensure_account(s, "1") is None  # 失败不抛,返回 None
    finally:
        await engine.dispose()
