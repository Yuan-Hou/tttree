"""管理控制台 API:管理员增删改用户 + 权限闸 + 封禁语义(真实鉴权链路)。

走临时 DB + 真实 token。变更类(建/改名/改密/封禁)经 session.get 命中 DB,故先把缓存用户落库。
"""

import httpx
import pytest

from app.auth import users as users_mod
from app.auth.passwords import hash_password
from app.auth.tokens import make_token
from app.auth.users import User
from app.db.models import User as UserRow
from app.db.session import create_all, make_engine, make_session_factory

pytestmark = pytest.mark.real_auth


def _setup(monkeypatch):
    monkeypatch.setattr("app.config.settings.app_secret", "test-admin-secret", raising=False)
    users_mod.set_users_for_test(
        {
            "1": User("1", "admin", hash_password("pw-admin"), is_admin=True),
            "2": User("2", "bob", hash_password("pw-bob")),
        }
    )


async def _client(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.db.session.async_session", Session)
    async with Session() as s:
        for u in users_mod.list_users():
            s.add(UserRow(id=u.id, name=u.name, password_hash=u.password_hash, is_admin=u.is_admin, banned=u.banned))
        await s.commit()
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _h(uid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(uid)}"}


async def test_non_admin_forbidden(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        assert (await c.get("/admin/users")).status_code == 401  # 无 token
        assert (await c.get("/admin/users", headers=_h("2"))).status_code == 403  # 非管理员
        assert (await c.get("/admin/users", headers=_h("1"))).status_code == 200


async def test_list_users_no_secrets(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        rows = (await c.get("/admin/users", headers=_h("1"))).json()
        assert {r["name"] for r in rows} == {"admin", "bob"}
        assert all("password" not in r and "password_hash" not in r for r in rows)


async def test_list_users_shows_newapi_proxy_username(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        from app.db.models import NewApiAccount
        from app.db.session import async_session

        async with async_session() as s:  # 只给 bob 建 new-api 子账号,admin 不建
            s.add(NewApiAccount(user_id="2", newapi_user_id=42, username="brand_2_ab12cd",
                                password="x", token_id=7, api_key="sk-x"))
            await s.commit()

        rows = (await c.get("/admin/users", headers=_h("1"))).json()
        by_name = {r["name"]: r for r in rows}
        assert by_name["bob"]["newapi_username"] == "brand_2_ab12cd"  # 有账号 → 显示代理名
        assert by_name["admin"]["newapi_username"] is None            # 无账号 → None
        assert all("password" not in r and "api_key" not in r for r in rows)


async def test_create_user_then_login(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        r = await c.post("/admin/users", json={"name": "carol", "password": "pw-carol"}, headers=_h("1"))
        assert r.status_code == 201
        new = r.json()
        assert new["name"] == "carol" and new["is_admin"] is False and new["banned"] is False
        # 新用户可登录
        assert (await c.post("/auth/login", json={"name": "carol", "password": "pw-carol"})).status_code == 200
        # 重名 → 409
        assert (await c.post("/admin/users", json={"name": "carol", "password": "x"}, headers=_h("1"))).status_code == 409


async def test_rename_user(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        r = await c.patch("/admin/users/2", json={"name": "robert"}, headers=_h("1"))
        assert r.status_code == 200 and r.json()["name"] == "robert"
        assert (await c.post("/auth/login", json={"name": "robert", "password": "pw-bob"})).status_code == 200
        assert (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).status_code == 401


async def test_admin_reset_password(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        r = await c.post("/admin/users/2/password", json={"new_password": "reset-pw"}, headers=_h("1"))
        assert r.status_code == 204
        assert (await c.post("/auth/login", json={"name": "bob", "password": "reset-pw"})).status_code == 200
        assert (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).status_code == 401


async def test_ban_blocks_login_and_existing_token(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        bob_tok = (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).json()["token"]
        bob_h = {"Authorization": f"Bearer {bob_tok}"}
        assert (await c.get("/auth/me", headers=bob_h)).status_code == 200  # 封禁前可用
        assert (await c.patch("/admin/users/2", json={"banned": True}, headers=_h("1"))).status_code == 200
        # 封禁后:既有 token 失效 + 重新登录失败
        assert (await c.get("/auth/me", headers=bob_h)).status_code == 401
        assert (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).status_code == 401
        # 解封 → 又可登录
        assert (await c.patch("/admin/users/2", json={"banned": False}, headers=_h("1"))).status_code == 200
        assert (await c.post("/auth/login", json={"name": "bob", "password": "pw-bob"})).status_code == 200


async def test_cannot_ban_self(tmp_path, monkeypatch):
    _setup(monkeypatch)
    async with await _client(tmp_path, monkeypatch) as c:
        assert (await c.patch("/admin/users/1", json={"banned": True}, headers=_h("1"))).status_code == 400
