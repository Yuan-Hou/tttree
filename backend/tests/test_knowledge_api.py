"""知识库 HTTP 壳(子步二):GET/PUT 往返 + 故事不存在 404。逻辑复用 M4.5-A store。"""

import httpx

from app.db.session import create_all, make_engine, make_session_factory
from app.stories.store import create_story


async def _client(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'kb.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.db.session.async_session", Session)
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t"), Session, engine


async def test_knowledge_roundtrip(tmp_path, monkeypatch):
    c, Session, engine = await _client(tmp_path, monkeypatch)
    async with c:
        async with Session() as s:
            sid = (await create_story(s, title="T")).id

        # 新故事默认空
        assert (await c.get(f"/story/{sid}/knowledge")).json()["content"] == ""

        text = "主角:林。世界观:潮汐之城。\n第二行设定。"
        p = await c.put(f"/story/{sid}/knowledge", json={"content": text})
        assert p.status_code == 200 and p.json()["content"] == text

        # 重新载入仍在(整篇无损)
        assert (await c.get(f"/story/{sid}/knowledge")).json()["content"] == text

        # 可清空
        await c.put(f"/story/{sid}/knowledge", json={"content": ""})
        assert (await c.get(f"/story/{sid}/knowledge")).json()["content"] == ""
    await engine.dispose()


async def test_knowledge_404_for_unknown_story(tmp_path, monkeypatch):
    c, _, engine = await _client(tmp_path, monkeypatch)
    async with c:
        assert (await c.get("/story/nope/knowledge")).status_code == 404
        assert (await c.put("/story/nope/knowledge", json={"content": "x"})).status_code == 404
    await engine.dispose()
