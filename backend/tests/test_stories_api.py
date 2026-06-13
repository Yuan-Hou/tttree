"""故事档案 API 冒烟测试(ASGI 直连,不起真服务器)。用临时 DB 隔离。"""

import httpx
import pytest

from app.db import session as db_session
from app.db.session import create_all, make_engine, make_session_factory


@pytest.fixture
async def client(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    # 让依赖与应用都用临时 DB
    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setattr(db_session, "async_session", Session)
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    await engine.dispose()


async def test_stories_crud_api(client):
    # 新建两个
    a = (await client.post("/stories", json={"title": "故事甲"})).json()
    b = (await client.post("/stories", json={"title": "故事乙"})).json()
    assert a["title"] == "故事甲" and a["turn_count"] == 0
    assert a["id"] != b["id"]

    # 列出
    lst = (await client.get("/stories")).json()
    assert {s["title"] for s in lst} == {"故事甲", "故事乙"}

    # 重命名
    r = await client.patch(f"/stories/{a['id']}", json={"title": "故事甲改"})
    assert r.json()["title"] == "故事甲改"

    # 删除
    d = await client.delete(f"/stories/{a['id']}")
    assert d.json()["ok"] is True
    lst2 = (await client.get("/stories")).json()
    assert {s["title"] for s in lst2} == {"故事乙"}

    # 删不存在 → 404
    assert (await client.delete("/stories/nope")).status_code == 404
    print("\n[stories-api] create/list/rename/delete + 404 全通。")
