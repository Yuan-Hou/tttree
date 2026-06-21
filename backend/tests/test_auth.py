"""Stage 0:鉴权地基(登录发 token / token 校验 / me)。

不打 DB(用户来自配置、token 来自 APP_SECRET)。用 ASGITransport 直连 app,monkeypatch 注入用户与密钥。
"""

import httpx
import pytest

from app.auth import users as users_mod
from app.auth.passwords import hash_password
from app.auth.users import User
from app.db.session import create_all, make_engine, make_session_factory

# 本模块自验真实鉴权(含 401 路径),不要被 conftest 的默认放行覆盖。
pytestmark = pytest.mark.real_auth


def _setup(monkeypatch):
    monkeypatch.setattr("app.config.settings.app_secret", "test-auth-secret", raising=False)
    users_mod.set_users_for_test(
        {
            "1": User("1", "admin", hash_password("pw-admin"), is_admin=True),
            "2": User("2", "bob", hash_password("pw-bob")),
        }
    )


async def _client(tmp_path, monkeypatch):
    # 登录会经 get_session 惰性补齐 new-api(conftest 已关真实补齐),需要一个带表的临时库,避免触碰真实 vore.db。
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    await create_all(engine)
    monkeypatch.setattr("app.db.session.async_session", make_session_factory(engine))
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_login_success(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        r = await c.post("/auth/login", json={"name": "admin", "password": "pw-admin"})
        assert r.status_code == 200
        body = r.json()
        assert body["uid"] == "1" and body["name"] == "admin" and body["token"]


async def test_login_bad_password(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        r = await c.post("/auth/login", json={"name": "admin", "password": "wrong"})
        assert r.status_code == 401


async def test_login_unknown_user(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        r = await c.post("/auth/login", json={"name": "ghost", "password": "x"})
        assert r.status_code == 401


async def test_me_requires_token(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        assert (await c.get("/auth/me")).status_code == 401


async def test_me_bad_token(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        r = await c.get("/auth/me", headers={"Authorization": "Bearer garbage.token"})
        assert r.status_code == 401


async def test_me_with_token(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        tok = (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).json()["token"]
        r = await c.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        assert r.json() == {"uid": "2", "name": "bob", "is_admin": False}


async def test_token_from_other_secret_rejected(tmp_path, monkeypatch):
    """换了 APP_SECRET 后旧 token 签名不再有效 → 401(不是 500)。"""
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        tok = (await c.post("/auth/login", json={"name": "admin", "password": "pw-admin"})).json()["token"]
    monkeypatch.setattr("app.config.settings.app_secret", "rotated-secret", raising=False)
    async with await _client(tmp_path, monkeypatch) as c:
        r = await c.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401


async def _client_with_db(tmp_path, monkeypatch):
    """像 _client,但同时把缓存里的用户落进临时 DB(自助/管理变更走 session.get 需要 DB 行)。"""
    from app.db.models import User as UserRow

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.db.session.async_session", Session)
    async with Session() as s:
        for u in users_mod.list_users():
            s.add(UserRow(id=u.id, name=u.name, password_hash=u.password_hash, is_admin=u.is_admin, banned=u.banned))
        await s.commit()
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_change_own_nickname(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client_with_db(tmp_path, monkeypatch) as c:
        tok = (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        r = await c.patch("/auth/me", json={"name": "bobby"}, headers=h)
        assert r.status_code == 200 and r.json()["name"] == "bobby"
        # 新名能登录、旧名不能
        assert (await c.post("/auth/login", json={"name": "bobby", "password": "pw-bob"})).status_code == 200
        assert (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).status_code == 401


async def test_change_nickname_conflict(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client_with_db(tmp_path, monkeypatch) as c:
        tok = (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).json()["token"]
        r = await c.patch("/auth/me", json={"name": "admin"}, headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 409


async def test_change_own_password(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client_with_db(tmp_path, monkeypatch) as c:
        tok = (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        # 旧口令错 → 400
        assert (await c.post("/auth/me/password", json={"old_password": "nope", "new_password": "x2"}, headers=h)).status_code == 400
        # 正确旧口令 → 204,随后新口令可登、旧口令不可登
        assert (await c.post("/auth/me/password", json={"old_password": "pw-bob", "new_password": "new-pw"}, headers=h)).status_code == 204
        assert (await c.post("/auth/login", json={"name": "bob", "password": "new-pw"})).status_code == 200
        assert (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).status_code == 401


def test_jwt_roundtrip(monkeypatch):
    monkeypatch.setattr("app.config.settings.app_secret", "s", raising=False)
    from app.auth import tokens

    tok = tokens.make_token("7")
    assert tokens.decode_uid(tok) == "7"
    assert tokens.decode_uid("not-a-token") is None


def test_jwt_without_secret_raises(monkeypatch):
    monkeypatch.setattr("app.config.settings.app_secret", None, raising=False)
    from app.auth import tokens

    with pytest.raises(tokens.AuthConfigError):
        tokens.make_token("1")
