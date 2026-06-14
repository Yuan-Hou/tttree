"""参考图库 CRUD HTTP 接口(M4.5-E 第二件;ASGI 直连,临时 DB 隔离)。"""

import json

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import ImageGen
from app.db.session import create_all, make_engine, make_session_factory
from app.stories.store import create_story

PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


@pytest.fixture
async def ctx(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'refapi.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    from app.main import app
    from app.web.deps import get_session

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, Session
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_references_crud(ctx):
    c, Session = ctx
    async with Session() as s:
        sid = (await create_story(s, title="S")).id

    # POST(multipart 上传)
    r = await c.post(f"/story/{sid}/references",
                     files={"file": ("p.png", PNG_1x1, "image/png")},
                     data={"label": "主角立绘", "description": "蓝发", "category": "角色"})
    assert r.status_code == 200, r.text
    asset = r.json()
    aid = asset["asset_id"]
    assert asset["label"] == "主角立绘" and asset["category"] == "角色" and asset["file_path"].startswith("storage/references/")

    # GET
    lst = (await c.get(f"/story/{sid}/references")).json()
    assert len(lst) == 1 and lst[0]["asset_id"] == aid

    # PATCH(改 label/description/category)
    r = await c.patch(f"/story/{sid}/references/{aid}",
                      json={"label": "白子立绘", "description": "改了说明", "category": "物品"})
    assert r.status_code == 200
    assert r.json()["label"] == "白子立绘" and r.json()["description"] == "改了说明" and r.json()["category"] == "物品"

    # DELETE
    assert (await c.delete(f"/story/{sid}/references/{aid}")).json()["ok"] is True
    assert (await c.get(f"/story/{sid}/references")).json() == []

    # 404:删不存在 / 别的故事 / 不存在的故事
    assert (await c.delete(f"/story/{sid}/references/{aid}")).status_code == 404
    assert (await c.get("/story/nope/references")).status_code == 404
    # 非法 category → 400
    bad = await c.post(f"/story/{sid}/references", files={"file": ("p.png", PNG_1x1, "image/png")},
                       data={"label": "x", "category": "不存在的分类"})
    assert bad.status_code == 400


async def test_delete_reference_keeps_history_imagegen_and_generated_file(ctx, tmp_path):
    """删一张被历史 ImageGen 引用过的参考图 → 历史 ImageGen 记录与其生成图文件仍在(只删素材本身)。"""
    c, Session = ctx
    async with Session() as s:
        sid = (await create_story(s, title="S")).id
    r = await c.post(f"/story/{sid}/references", files={"file": ("p.png", PNG_1x1, "image/png")},
                     data={"label": "立绘", "category": "角色"})
    aid = r.json()["asset_id"]

    # 历史上用它生成过一张图:ImageGen.ref_asset_ids 含 aid;生成图是独立资产(放 tmp 模拟)
    gen_file = tmp_path / "g.png"
    gen_file.write_bytes(b"GENERATED")
    async with Session() as s:
        s.add(ImageGen(story_id=sid, scene_slug="room", kind="new_scene",
                       ref_asset_ids=json.dumps([aid]), ref_image_paths="[]",
                       output_path=str(gen_file), origin="user_initiated", source_turn=1))
        await s.commit()

    # 删参考图
    assert (await c.delete(f"/story/{sid}/references/{aid}")).json()["ok"] is True
    assert (await c.get(f"/story/{sid}/references")).json() == []   # 参考图素材已删

    # 历史 ImageGen 记录仍在 + 其生成图文件仍在(delete_reference 不碰 ImageGen / storage/images)
    async with Session() as s:
        n = (await s.execute(select(func.count()).select_from(ImageGen).where(ImageGen.story_id == sid))).scalar()
    assert n == 1
    assert gen_file.exists()
