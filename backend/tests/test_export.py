"""故事导出(只读单文件 HTML)。验证导出接口:把当前快照 + 场景地图注入查看器模板,
图片内联为压缩后的 webp data: URI;创作要素不参与(纯只读路径)。

模板用临时桩文件(不依赖前端真实构建产物);图片用临时 PNG(monkeypatch 路径解析到 tmp)。"""

import base64
import io
import json

import httpx
from PIL import Image

from app.db.models import Blackboard, ImageGen, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.imaging.pipeline import CANON_ORIGIN
from app.stories.store import create_story

SCENES = {
    "hall": {
        "name": "门厅",
        "origin_turn": 1,
        "image_paths": ["storage/images/hall.png"],
        "connections": [],
    },
}


def _bb() -> dict:
    return {
        "story_meta": {"title": "T", "current_scene": "hall"},
        "scenes": SCENES,
        "characters": {},
        "items": {},
        "notes": [],
    }


async def _setup(tmp_path, monkeypatch, *, with_image=True):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'export.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    # get_snapshot 绑定的是 turn_router.async_session;scene-map/Story 经 deps.get_session 取 db.session.async_session
    monkeypatch.setattr("app.web.turn_router.async_session", Session)
    monkeypatch.setattr("app.db.session.async_session", Session)

    # 查看器模板桩:含 <meta charset>(注入锚点)+ type=module 脚本占位
    template = tmp_path / "viewer.html"
    template.write_text(
        '<!doctype html><html><head><meta charset="UTF-8" /><title>t</title></head>'
        '<body><div id="root"></div><script type="module">/*app*/</script></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.web.export_router._VIEWER_TEMPLATE", template)

    # 图片路径解析重定向到 tmp,避免污染真实 storage;造一张 2000px 大图以验证缩放
    storage = tmp_path / "store"
    monkeypatch.setattr("app.web.export_router.abs_from_rel", lambda rel: storage / rel)
    if with_image:
        p = storage / "storage" / "images"
        p.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2000, 1500), (120, 80, 40)).save(p / "hall.png")

    async with Session() as s:
        sid = (await create_story(s, title="海的故事")).id
        bb = await s.get(Blackboard, sid)
        bb.json_blob = json.dumps(_bb(), ensure_ascii=False)
        s.add(
            Turn(
                story_id=sid,
                turn_index=1,
                beat_title="抵达",
                user_input="走进门厅",
                narrative="叙事正文。",
                director_a_json="{}",
                director_b_json="{}",
                blackboard_after=json.dumps(_bb(), ensure_ascii=False),
            )
        )
        s.add(
            ImageGen(
                story_id=sid,
                scene_slug="hall",
                kind="new_scene",
                output_path="storage/images/hall.png",
                origin=CANON_ORIGIN,
                source_turn=1,
                superseded=False,
            )
        )
        await s.commit()
    return engine, sid


async def test_export_returns_selfcontained_html_with_inlined_data(tmp_path, monkeypatch):
    engine, sid = await _setup(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=__import__("app.main", fromlist=["app"]).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(f"/story/{sid}/export", json={"layout": {"hall": {"x": 123, "y": 456}}})
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        # 文件名用故事标题(RFC5987 百分号编码)
        assert "filename*=UTF-8''" in r.headers["content-disposition"]
        body = r.text

        # 冻结快照注入到 type=module 脚本之前,且无原始裸 `<` 截断风险
        assert "window.__VORE_EXPORT__=" in body
        assert body.index("window.__VORE_EXPORT__=") < body.index("<script type=\"module\">")
        # 且注入点在 <meta charset> 之后 —— 保编码声明留在文档前 1024 字节,手机端 file:// 不乱码
        assert body.index("charset") < body.index("window.__VORE_EXPORT__=")

        # 叙事文本随快照一同导出
        assert "叙事正文。" in body
        # 图片内联为 webp data: URI,且没有残留指向后端的 /storage 路径(全部已内联)
        assert "data:image/webp;base64," in body
        assert "storage/images/hall.png" not in body
        # 地图布局随导出带上
        assert "\"layout\"" in body and "123" in body and "456" in body
    await engine.dispose()


async def test_export_image_downscaled_to_cap(tmp_path, monkeypatch):
    from app.web.export_router import _MAX_EDGE, _to_data_uri

    engine, _ = await _setup(tmp_path, monkeypatch)
    uri = _to_data_uri("storage/images/hall.png", {})
    assert uri and uri.startswith("data:image/webp;base64,")
    raw = base64.b64decode(uri.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as im:
        assert max(im.size) <= _MAX_EDGE  # 2000 → ≤1280
        assert im.format == "WEBP"
    await engine.dispose()


async def test_export_404_for_missing_story(tmp_path, monkeypatch):
    engine, _ = await _setup(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=__import__("app.main", fromlist=["app"]).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        assert (await c.post("/story/nope/export", json={"layout": {}})).status_code == 404
    await engine.dispose()
