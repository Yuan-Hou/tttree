"""Stage 1:故事归属 + 路由鉴权 + 数据隔离(真实 token 链路)。

验证:列表按 owner 隔离、跨用户访问 404(不泄露存在性)、无 token 401、本人可访问。
"""

import httpx
import pytest

from app.auth.tokens import make_token
from app.db.session import create_all, make_engine, make_session_factory
from app.stories.store import create_story

pytestmark = pytest.mark.real_auth


def _auth(uid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(uid)}"}


async def _client(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'iso.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.db.session.async_session", Session)
    from app.main import app

    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    return c, Session, engine


async def test_story_list_is_owner_isolated(tmp_path, monkeypatch):
    c, Session, engine = await _client(tmp_path, monkeypatch)
    try:
        async with Session() as s:
            mine = (await create_story(s, title="mine", owner_id="1")).id
            theirs = (await create_story(s, title="theirs", owner_id="2")).id
        async with c:
            ids1 = {x["id"] for x in (await c.get("/stories", headers=_auth("1"))).json()}
            assert mine in ids1 and theirs not in ids1
            ids2 = {x["id"] for x in (await c.get("/stories", headers=_auth("2"))).json()}
            assert theirs in ids2 and mine not in ids2
    finally:
        await engine.dispose()


async def test_cross_user_access_is_404(tmp_path, monkeypatch):
    c, Session, engine = await _client(tmp_path, monkeypatch)
    try:
        async with Session() as s:
            sid = (await create_story(s, title="mine", owner_id="1")).id
        async with c:
            # 本人:get_session 路由放行(与归属闸同库)
            assert (await c.get(f"/story/{sid}/knowledge", headers=_auth("1"))).status_code == 200
            # 他人:读 404、写 404(归属闸在任何副作用前拦下,不泄露存在性)
            assert (await c.get(f"/story/{sid}/knowledge", headers=_auth("2"))).status_code == 404
            r = await c.post(f"/story/{sid}/turn", json={"user_input": "hi"}, headers=_auth("2"))
            assert r.status_code == 404
    finally:
        await engine.dispose()


async def test_routes_require_token(tmp_path, monkeypatch):
    c, Session, engine = await _client(tmp_path, monkeypatch)
    try:
        async with Session() as s:
            sid = (await create_story(s, title="mine", owner_id="1")).id
        async with c:
            assert (await c.get("/stories")).status_code == 401
            assert (await c.get(f"/story/{sid}/snapshot")).status_code == 401
            assert (await c.get("/global-settings")).status_code == 401
            # 公共端点不需登录
            assert (await c.get("/health")).status_code == 200
    finally:
        await engine.dispose()


async def test_fork_keeps_owner(tmp_path, monkeypatch):
    c, Session, engine = await _client(tmp_path, monkeypatch)
    try:
        async with Session() as s:
            sid = (await create_story(s, title="mine", owner_id="1")).id
        async with c:
            r = await c.post(f"/stories/{sid}/fork", headers=_auth("1"))
            assert r.status_code == 200
            new_id = r.json()["id"]
            # 副本归同一用户:1 号能列到、2 号列不到
            ids2 = {x["id"] for x in (await c.get("/stories", headers=_auth("2"))).json()}
            assert new_id not in ids2
    finally:
        await engine.dispose()
