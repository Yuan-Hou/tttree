"""写稿节点 / 画图节点真正分开 + 参考图自由选择(绘图节点修整)。

draw_router 用 async_session() 直连 → monkeypatch 指向临时库;run_illustrator / execute_image
monkeypatch 掉(不打真 LLM / 不出图),并捕获写稿输入以验证「编辑后重写」。
"""

import json

import httpx
import pytest
from sqlalchemy import select

from app.db.models import DrawProposal, ImageGen, ReferenceAsset, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.models.schemas import IllustratorDraft, ReferenceRef
from app.stories.store import create_story


def _bb(scenes):
    return json.dumps({"story_meta": {"title": "T"}, "scenes": scenes, "characters": {}, "items": {}, "notes": []})


def _sc(origin):
    return {"name": "场景", "state": "", "image_paths": [], "origin_turn": origin}


@pytest.fixture
async def env(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'split.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.web.draw_router.async_session", Session)

    cap = {}

    async def fake_illustrator(*, history, blackboard, draw_request, reference_catalog, visual_style=None, messages=None, model=None):
        cap["messages"] = messages
        return IllustratorDraft(
            kind="reuse",  # 故意错:应被后端按提案 kind 覆盖
            prompt_text="一段画面提示词",
            reference_manifest=[ReferenceRef(semantic_name="主角立绘", source="reference_asset", asset_id=1, purpose="锚定角色")],
        )

    monkeypatch.setattr("app.imaging.draw_service.run_illustrator", fake_illustrator)

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, Session, cap
    await engine.dispose()


async def _seed(Session, sid):
    async with Session() as s:
        s.add(Turn(story_id=sid, turn_index=1, user_input="a1", narrative="n1", director_a_json="{}",
                   director_b_json="{}", blackboard_after=_bb({"X": _sc(1)})))
        s.add(Turn(story_id=sid, turn_index=2, user_input="a2", narrative="n2", director_a_json="{}",
                   director_b_json="{}", blackboard_after=_bb({"X": _sc(1)})))
        s.add(ReferenceAsset(story_id=sid, label="主角立绘", description="蓝发", category="角色",
                             file_path="storage/references/hero.png"))
        await s.commit()


async def test_write_outputs_prompt_not_image(env):
    """写稿节点:输出是提示词文本(非图),kind 被后端覆盖为提案的 new_scene,落库持久化。"""
    c, Session, _ = env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await _seed(Session, sid)
    async with Session() as s:
        s.add(DrawProposal(story_id=sid, scene_slug="X", origin_proposal_turn=1, kind="new_scene", status="pending"))
        await s.commit()
        pid = (await s.execute(select(DrawProposal.id))).scalar_one()

    r = await c.post(f"/story/{sid}/draw/proposal/{pid}/write", json={})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["draft_prompt"] == "一段画面提示词" and j["kind"] == "new_scene"
    assert "image" not in j and "output_path" not in j  # 写稿不产图
    assert j["draft_manifest"][0]["semantic_name"] == "主角立绘"
    # 持久化:GET 能取回写稿稿 + 两类参考图来源
    g = (await c.get(f"/story/{sid}/draw/proposal/{pid}")).json()
    assert g["draft_prompt"] == "一段画面提示词"
    assert any(a["asset_id"] == 1 for a in g["library"])  # 图库来源
    assert "past_images" in g  # 过往结果来源


async def test_write_retry_uses_edited_messages(env):
    """写稿重试用编辑后的输入(像三段式节点)。"""
    c, Session, cap = env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await _seed(Session, sid)
    async with Session() as s:
        s.add(DrawProposal(story_id=sid, scene_slug="X", origin_proposal_turn=1, kind="new_scene", status="pending"))
        await s.commit()
        pid = (await s.execute(select(DrawProposal.id))).scalar_one()

    edited = [{"role": "system", "content": "改过的写稿系统"}, {"role": "user", "content": "改过的请求"}]
    await c.post(f"/story/{sid}/draw/proposal/{pid}/write", json={"messages": edited})
    assert cap["messages"] == edited  # 绘图 Agent 收到的是编辑后的输入


async def test_picture_records_chosen_refs_and_marks_done(env, monkeypatch):
    """画图节点:据用户选的两类参考图出图;ImageGen 记 ref_asset_ids+ref_image_paths;提案置 done。"""
    from app.imaging.executor import ExecResult

    async def fake_execute(*, final_prompt, ref_files, scene_slug, **kw):
        return ExecResult(output_path="storage/images/new.png", api_call="edit", ref_files_sent=[str(p) for p in ref_files])

    monkeypatch.setattr("app.imaging.draw_service.execute_image", fake_execute)

    c, Session, _ = env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await _seed(Session, sid)
    async with Session() as s:
        # 该场景已有 new_scene 基底(让 variant 可画)+ 一张过往结果可选
        s.add(ImageGen(story_id=sid, scene_slug="X", kind="new_scene", output_path="storage/images/base.png", source_turn=1))
        s.add(DrawProposal(story_id=sid, scene_slug="X", origin_proposal_turn=2, kind="variant", status="pending"))
        await s.commit()
        pid = (await s.execute(select(DrawProposal.id).where(DrawProposal.kind == "variant"))).scalar_one()

    body = {
        "prompt": "用户最终提示词",
        "references": [
            {"semantic_name": "主角立绘", "source": "reference_asset", "asset_id": 1, "purpose": "角色"},
            {"semantic_name": "X·初见", "source": "history_image", "image_path": "storage/images/base.png", "purpose": "保持布局"},
        ],
    }
    r = await c.post(f"/story/{sid}/draw/proposal/{pid}/picture", json=body)
    assert r.status_code == 200
    _ = r.text  # 读完短命流 → 生成器跑完

    async with Session() as s:
        p = await s.get(DrawProposal, pid)
        ig = (await s.execute(select(ImageGen).where(ImageGen.output_path == "storage/images/new.png"))).scalar_one()
    assert p.status == "done" and p.done_image_id == ig.id
    assert json.loads(ig.ref_asset_ids) == [1]
    assert json.loads(ig.ref_image_paths) == ["storage/images/base.png"]
    assert ig.kind == "variant" and ig.final_prompt == "用户最终提示词"


async def test_picture_variant_gated_without_base(env, monkeypatch):
    """画图节点:variant 无 new_scene 基底 → image_failed(确认闸门后的执行仍被门控)。"""
    async def fake_execute(**kw):
        raise AssertionError("不应触达 execute_image")

    monkeypatch.setattr("app.imaging.draw_service.execute_image", fake_execute)

    c, Session, _ = env
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
    await _seed(Session, sid)
    async with Session() as s:
        s.add(DrawProposal(story_id=sid, scene_slug="X", origin_proposal_turn=2, kind="variant", status="pending"))
        await s.commit()
        pid = (await s.execute(select(DrawProposal.id))).scalar_one()

    r = await c.post(f"/story/{sid}/draw/proposal/{pid}/picture", json={"prompt": "p", "references": []})
    assert r.status_code == 200
    assert "image_failed" in r.text and "基底" in r.text