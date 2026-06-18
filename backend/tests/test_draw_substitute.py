"""替代图片(旁路):不调 gpt-image-2,由用户直接指定一张图作为本次出图结果。

验证:① 走正典提案入口 → origin=director_b_proposal、进黑板、按 (scene, turn) 取代旧正典图、
提案标 done;② 走手动入口 → origin=user_initiated、不进黑板;③ 不论选已有图还是上传,都**复制
成新文件**(新路径、与源文件解耦,源文件仍在);④ 全程**未调用 execute_image**(没花钱)。
"""

import json

import httpx
from sqlalchemy import select

from app.db.models import Blackboard, DrawProposal, ImageGen, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.stories.store import create_story


def _bb(scenes: dict) -> str:
    return json.dumps(
        {"story_meta": {"title": "T"}, "scenes": scenes, "characters": {}, "items": {}, "notes": []},
        ensure_ascii=False,
    )


def _scene_X(image_paths=None, origin=1):
    return {"X": {"name": "场景X", "state": "", "image_paths": image_paths or [], "origin_turn": origin}}


def _boom_exec(*a, **kw):  # execute_image 一旦被调到就炸 —— 替代图片绝不该走它
    raise AssertionError("execute_image 不应在替代图片路径被调用")


async def _setup(tmp_path, monkeypatch):
    """tmp_path 当 BACKEND_ROOT(文件落 tmp,不污染真实 storage);建故事 + 第1轮 + 一张源图文件。"""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'sub.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.web.draw_router.async_session", Session)
    monkeypatch.setattr("app.web.draw_router.BACKEND_ROOT", tmp_path)
    monkeypatch.setattr("app.imaging.draw_service.BACKEND_ROOT", tmp_path)
    monkeypatch.setattr("app.imaging.draw_service.execute_image", _boom_exec)

    (tmp_path / "storage" / "images").mkdir(parents=True)
    (tmp_path / "storage" / "images" / "src.png").write_bytes(b"SOURCE-PIXELS")

    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        bbX = _bb(_scene_X())
        (await s.get(Blackboard, sid)).json_blob = bbX
        s.add(Turn(story_id=sid, turn_index=1, user_input="u", narrative="n",
                   director_a_json="{}", director_b_json="{}", blackboard_after=bbX))
        # 库内「过往生成结果」,作为 ①选已有图 的源
        s.add(ImageGen(story_id=sid, scene_slug="X", kind="new_scene",
                       output_path="storage/images/src.png", origin="user_initiated", source_turn=1))
        await s.commit()
        src_id = (await s.execute(select(ImageGen.id))).scalar_one()
    return engine, Session, sid, src_id


async def test_substitute_proposal_entry_is_canon_and_supersedes(tmp_path, monkeypatch):
    """正典提案入口:选一张过往图替代 → 进黑板、新文件、取代旧正典图、提案 done、execute_image 未调。"""
    engine, Session, sid, src_id = await _setup(tmp_path, monkeypatch)
    async with Session() as s:
        # 该场景该轮已有一张正典图(待被取代)
        s.add(ImageGen(story_id=sid, scene_slug="X", kind="new_scene",
                       output_path="storage/images/old_canon.png", origin="director_b_proposal", source_turn=1))
        s.add(DrawProposal(story_id=sid, scene_slug="X", origin_proposal_turn=1, kind="new_scene", status="pending"))
        await s.commit()
        pid = (await s.execute(select(DrawProposal.id))).scalar_one()

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(f"/story/{sid}/draw/substitute",
                         data={"proposal_id": str(pid), "imagegen_id": str(src_id)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "image_substituted" and body["api_call"] == "substitute"
    new_path = body["output_path"]

    # 复制成新文件:新路径 ≠ 源路径,新文件存在且内容一致,源文件仍在(copy 非 move)
    assert new_path != "storage/images/src.png"
    assert (tmp_path / new_path).read_bytes() == b"SOURCE-PIXELS"
    assert (tmp_path / "storage" / "images" / "src.png").exists()

    async with Session() as s:
        ig = await s.get(ImageGen, body["imagegen_id"])
        bb = json.loads((await s.get(Blackboard, sid)).json_blob)
        old = (await s.execute(
            select(ImageGen).where(ImageGen.output_path == "storage/images/old_canon.png")
        )).scalar_one()
        prop = await s.get(DrawProposal, pid)
    assert ig.origin == "director_b_proposal" and ig.kind == "new_scene"
    assert new_path in bb["scenes"]["X"]["image_paths"]  # 进黑板
    assert old.superseded is True                        # 同轮同场景旧正典图被取代
    assert prop.status == "done" and prop.done_image_id == body["imagegen_id"]
    await engine.dispose()


async def test_substitute_manual_entry_upload_not_in_blackboard(tmp_path, monkeypatch):
    """手动入口 + 上传新图替代:origin=user_initiated、不进黑板、上传内容落成新文件、execute_image 未调。"""
    engine, Session, sid, _src_id = await _setup(tmp_path, monkeypatch)

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            f"/story/{sid}/draw/substitute",
            data={"scene": "X", "source": "user_initiated", "source_turn": "1"},
            files={"file": ("up.png", b"UPLOADED-PIXELS", "image/png")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    new_path = body["output_path"]
    assert (tmp_path / new_path).read_bytes() == b"UPLOADED-PIXELS"  # 上传内容落新文件

    async with Session() as s:
        ig = await s.get(ImageGen, body["imagegen_id"])
        bb = json.loads((await s.get(Blackboard, sid)).json_blob)
    assert ig.origin == "user_initiated"
    assert new_path not in bb["scenes"]["X"]["image_paths"]  # 手动草稿不进黑板
    await engine.dispose()


async def test_substitute_requires_exactly_one_source(tmp_path, monkeypatch):
    """来源校验:既不给图也不给上传 → 400(既给图又给上传同样 400)。"""
    engine, Session, sid, src_id = await _setup(tmp_path, monkeypatch)

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r0 = await c.post(f"/story/{sid}/draw/substitute", data={"scene": "X", "source_turn": "1"})
        r2 = await c.post(
            f"/story/{sid}/draw/substitute",
            data={"scene": "X", "source_turn": "1", "imagegen_id": str(src_id)},
            files={"file": ("up.png", b"X", "image/png")},
        )
    assert r0.status_code == 400 and r2.status_code == 400
    await engine.dispose()
