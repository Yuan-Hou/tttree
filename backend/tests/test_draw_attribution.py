"""绘图归属修整:用户手动图(origin=user_initiated)vs Director-B 提案图(director_b_proposal)
彻底分清。手动图=私人草稿(不进黑板、对 Agent 隐身);提案图=故事正典(进黑板、进 Agent 候选池)。
两者一律按 origin 字段判定。
"""

import json

import httpx
from sqlalchemy import select

from app.db.models import Blackboard, DrawProposal, ImageGen, ReferenceAsset, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.imaging.draw_service import DraftBundle, apply_decision, build_history_catalog
from app.imaging.executor import ExecResult, ResolvedRefs
from app.models.schemas import IllustratorDraft, ReferenceRef
from app.stories.store import create_story
from app.turns.rollback import rollback_latest_turn


def _bb(scenes: dict) -> str:
    return json.dumps(
        {"story_meta": {"title": "T"}, "scenes": scenes, "characters": {}, "items": {}, "notes": []},
        ensure_ascii=False,
    )


def _scene_X(image_paths=None, origin=1):
    return {"X": {"name": "场景X", "state": "", "image_paths": image_paths or [], "origin_turn": origin}}


def _bundle(kind="new_scene"):
    return DraftBundle(
        scene_slug="X",
        draft=IllustratorDraft(kind=kind, prompt_text="p", reference_manifest=[]),
        resolved=ResolvedRefs(),
        history=[],
    )


async def _story_with_scene_X(Session) -> str:
    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        bb = await s.get(Blackboard, sid)
        bb.json_blob = _bb(_scene_X())
        await s.commit()
    return sid


async def test_manual_out_of_blackboard_and_agent_pool_canon_in_both(tmp_path, monkeypatch):
    """手动图:origin=user_initiated、不进 image_paths、不进 Agent 候选池;提案图反之。"""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'attr.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    sid = await _story_with_scene_X(Session)

    outs = iter(["storage/images/manual.png", "storage/images/canon.png"])

    async def fake_exec(*, final_prompt, ref_files, scene_slug, **kw):
        return ExecResult(output_path=next(outs), api_call="generate", ref_files_sent=[])

    monkeypatch.setattr("app.imaging.draw_service.execute_image", fake_exec)

    # ── 用户手动图 ──
    async with Session() as s:
        res_m = await apply_decision(
            s, decision="confirm", bundle=_bundle(), final_prompt="p",
            story_id=sid, origin="user_initiated", source_turn=1,
        )
    async with Session() as s:
        ig_m = await s.get(ImageGen, res_m["imagegen_id"])
        bb = json.loads((await s.get(Blackboard, sid)).json_blob)
        cat = await build_history_catalog(s, sid, "X", "场景X")
    assert ig_m.origin == "user_initiated"
    assert "storage/images/manual.png" not in bb["scenes"]["X"]["image_paths"]  # 不进黑板
    assert cat == []  # 对绘图 Agent 隐身

    # ── Director-B 提案图(正典)──
    async with Session() as s:
        await apply_decision(
            s, decision="confirm", bundle=_bundle(), final_prompt="p",
            story_id=sid, origin="director_b_proposal", source_turn=1,
        )
    async with Session() as s:
        bb2 = json.loads((await s.get(Blackboard, sid)).json_blob)
        cat2 = await build_history_catalog(s, sid, "X", "场景X")
    assert "storage/images/canon.png" in bb2["scenes"]["X"]["image_paths"]  # 进黑板
    pool = [c["image_path"] for c in cat2]
    assert "storage/images/canon.png" in pool  # 进 Agent 候选池
    assert "storage/images/manual.png" not in pool  # 手动图仍被排除
    await engine.dispose()


async def test_snapshot_drafts_and_refpicker_lists_manual(tmp_path, monkeypatch):
    """界面分流:snapshot.scenes_drafts 含手动图(标非正式)、scenes_images 只含正典;
    RefPicker 过往结果(get_proposal_draw.past_images)全列、含手动图(用户能手动选)。"""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'attr_http.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.web.turn_router.async_session", Session)
    monkeypatch.setattr("app.web.draw_router.async_session", Session)

    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        bbX = _bb(_scene_X(image_paths=["storage/images/canon.png"]))
        bb = await s.get(Blackboard, sid)
        bb.json_blob = bbX
        s.add(Turn(story_id=sid, turn_index=1, user_input="u", narrative="n",
                   director_a_json="{}", director_b_json="{}", blackboard_after=bbX))
        s.add(ImageGen(story_id=sid, scene_slug="X", kind="new_scene",
                       output_path="storage/images/manual.png", origin="user_initiated", source_turn=1))
        s.add(ImageGen(story_id=sid, scene_slug="X", kind="new_scene",
                       output_path="storage/images/canon.png", origin="director_b_proposal", source_turn=1))
        s.add(DrawProposal(story_id=sid, scene_slug="X", origin_proposal_turn=1, kind="variant", status="pending"))
        await s.commit()
        pid = (await s.execute(select(DrawProposal.id))).scalar_one()

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        snap = (await c.get(f"/story/{sid}/snapshot")).json()
        assert snap["scenes_images"]["X"] == ["storage/images/canon.png"]    # 正典进黑板
        assert snap["scenes_drafts"]["X"] == ["storage/images/manual.png"]   # 手动图单列
        pd = (await c.get(f"/story/{sid}/draw/proposal/{pid}")).json()
        past = [p["output_path"] for p in pd["past_images"]]
        assert "storage/images/manual.png" in past and "storage/images/canon.png" in past
    await engine.dispose()


async def test_manual_draw_can_edit_refs_and_confirm_records_choice(tmp_path, monkeypatch):
    """手动绘图稿:/draw 返回图库 + 过往结果(全列,可调参考图);confirm 带编辑后的清单出图,
    ImageGen 记录用户所选参考图、origin=user_initiated、仍不进黑板。"""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'attr_manual.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.web.draw_router.async_session", Session)

    async def fake_illustrator(*, history, blackboard, draw_request, reference_catalog, visual_style=None, messages=None, model=None, tips=None):
        return IllustratorDraft(
            kind="reuse", prompt_text="稿",
            reference_manifest=[ReferenceRef(semantic_name="主角立绘", source="reference_asset", asset_id=1, purpose="锚定")],
        )

    async def fake_exec(*, final_prompt, ref_files, scene_slug, **kw):
        return ExecResult(output_path="storage/images/manual.png", api_call="edit", ref_files_sent=[str(p) for p in ref_files])

    monkeypatch.setattr("app.imaging.draw_service.run_illustrator", fake_illustrator)
    monkeypatch.setattr("app.imaging.draw_service.execute_image", fake_exec)

    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        bbX = _bb(_scene_X())
        bb = await s.get(Blackboard, sid)
        bb.json_blob = bbX
        s.add(Turn(story_id=sid, turn_index=1, user_input="u", narrative="n",
                   director_a_json="{}", director_b_json="{}", blackboard_after=bbX))
        s.add(ReferenceAsset(story_id=sid, label="主角立绘", description="蓝发", category="角色",
                             file_path="storage/references/hero.png"))
        s.add(ImageGen(story_id=sid, scene_slug="X", kind="new_scene",
                       output_path="storage/images/old_manual.png", origin="user_initiated", source_turn=1))
        await s.commit()

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        d = (await c.post(f"/story/{sid}/draw", json={"scene": "X", "source": "user_initiated", "source_turn": 1})).json()
        assert d["type"] == "draft_ready"
        assert any(a["asset_id"] == 1 for a in d["library"])  # 图库来源
        assert any(p["output_path"] == "storage/images/old_manual.png" for p in d["past_images"])  # 过往结果全列(含手动图)

        body = {"draft_id": d["draft_id"], "decision": "confirm", "prompt": "最终词",
                "references": [{"semantic_name": "主角立绘", "source": "reference_asset", "asset_id": 1, "purpose": "锚定"}]}
        r = await c.post(f"/story/{sid}/draw/confirm", json=body)
        assert r.status_code == 200
        _ = r.text  # 读完短命 SSE

    async with Session() as s:
        ig = (await s.execute(select(ImageGen).where(ImageGen.output_path == "storage/images/manual.png"))).scalar_one()
        bb = json.loads((await s.get(Blackboard, sid)).json_blob)
    assert ig.origin == "user_initiated"
    assert json.loads(ig.ref_asset_ids) == [1] and ig.final_prompt == "最终词"  # 记录用户编辑后的参考图选择
    assert "storage/images/manual.png" not in bb["scenes"]["X"]["image_paths"]  # 仍不进黑板
    await engine.dispose()


async def test_rollback_with_manual_image_no_error_asset_kept(tmp_path):
    """一致性:回退涉及场景 X 时,手动图不在黑板 → 无「解除不存在引用」报错;ImageGen 记录保留。"""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'attr_rb.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)

    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        s.add(Turn(story_id=sid, turn_index=1, user_input="u1", narrative="n1",
                   director_a_json="{}", director_b_json="{}", blackboard_after=_bb({})))
        bbX = _bb(_scene_X(origin=2))
        s.add(Turn(story_id=sid, turn_index=2, user_input="u2", narrative="n2",
                   director_a_json="{}", director_b_json="{}", blackboard_after=bbX))
        bb = await s.get(Blackboard, sid)
        bb.json_blob = bbX
        s.add(ImageGen(story_id=sid, scene_slug="X", kind="new_scene",
                       output_path="storage/images/manual.png", origin="user_initiated", source_turn=2))
        await s.commit()

    async with Session() as s:
        r = await rollback_latest_turn(s, sid)
    assert r.ok and r.rolled_back_turn == 2 and r.new_latest_turn == 1

    async with Session() as s:
        bb = json.loads((await s.get(Blackboard, sid)).json_blob)
        igs = (await s.execute(select(ImageGen).where(ImageGen.scene_slug == "X"))).scalars().all()
    assert "X" not in bb["scenes"]  # 场景随回退消失
    assert len(igs) == 1 and igs[0].origin == "user_initiated"  # 手动图 ImageGen 保留,无误删
    await engine.dispose()
