"""出图前编辑参考图清单(M4.5-E 第一件):执行层按用户最终清单传图、ImageGen 审计用户最终选择。"""

import json
from pathlib import Path

from sqlalchemy import select

from app.db.models import ImageGen
from app.db.session import create_all, make_engine, make_session_factory
from app.imaging.draw_service import DraftBundle, apply_decision
from app.imaging.executor import ExecResult, ResolvedRefs, resolve_references
from app.models.schemas import IllustratorDraft, ReferenceRef
from app.storage import BACKEND_ROOT
from app.stories.store import create_story


class _Asset:
    def __init__(self, id_, fp):
        self.id, self.file_path = id_, fp


def test_resolve_edited_manifest_maps_user_choice():
    """用户编辑后的清单(删 Agent 的、加库里另一张 + 一张历史图)→ 正确映射文件/asset_ids。"""
    assets = {1: _Asset(1, "storage/references/a.png"), 2: _Asset(2, "storage/references/b.png")}
    edited = [
        ReferenceRef(semantic_name="立绘B", source="reference_asset", asset_id=2, purpose="加上库里的B"),
        ReferenceRef(semantic_name="旧教室·初见", source="history_image",
                     image_path="storage/images/h.png", purpose="保持布局"),
    ]
    resolved = resolve_references(edited, assets)
    assert resolved.asset_ids == [2]                       # Agent 的 asset1 被删,只剩用户选的 2
    assert resolved.image_paths == ["storage/images/h.png"]
    assert [str(f) for f in resolved.files] == [
        str(BACKEND_ROOT / "storage/references/b.png"),
        str(BACKEND_ROOT / "storage/images/h.png"),
    ]


async def test_apply_decision_uses_edited_resolved(tmp_path, monkeypatch):
    """confirm 传入编辑后的 resolved → execute_image 收到编辑后的文件、ImageGen 记编辑后的选择。"""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'edit.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    async with Session() as s:
        sid = (await create_story(s, title="S")).id

    captured = {}

    async def fake_execute_image(*, final_prompt, ref_files, scene_slug, **kw):
        captured["ref_files"] = list(ref_files)
        captured["prompt"] = final_prompt
        return ExecResult(output_path="storage/images/x.png", api_call="edit",
                          ref_files_sent=[str(p) for p in ref_files])

    monkeypatch.setattr("app.imaging.draw_service.execute_image", fake_execute_image)

    # Agent 原始清单 = [asset1];用户编辑后 = [asset2 + 一张历史图]
    bundle = DraftBundle(
        scene_slug="room",
        draft=IllustratorDraft(kind="new_scene", prompt_text="原稿", reference_manifest=[]),
        resolved=ResolvedRefs(asset_ids=[1], image_paths=[], files=[Path("/agent/asset1.png")]),
        history=[],
    )
    edited = ResolvedRefs(asset_ids=[2], image_paths=["storage/images/h.png"],
                          files=[Path("/lib/asset2.png"), Path("/hist/h.png")])

    async with Session() as s:
        res = await apply_decision(s, decision="confirm", bundle=bundle, final_prompt="用户改过的提示词",
                                   story_id=sid, origin="user_initiated", resolved=edited)

    # 执行层按用户编辑后的清单传图(不是 Agent 的 asset1)
    assert captured["ref_files"] == [Path("/lib/asset2.png"), Path("/hist/h.png")]
    assert captured["prompt"] == "用户改过的提示词"
    assert res["action"] == "confirm"
    # ImageGen 审计反映用户最终选择
    async with Session() as s:
        ig = (await s.execute(select(ImageGen).where(ImageGen.story_id == sid))).scalar_one()
    assert json.loads(ig.ref_asset_ids) == [2]
    assert json.loads(ig.ref_image_paths) == ["storage/images/h.png"]


async def test_apply_decision_defaults_to_agent_resolved_when_no_edit(tmp_path, monkeypatch):
    """未传 resolved(用户没编辑参考图)→ 仍用 Agent 原始清单(向后兼容)。"""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'edit2.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    async with Session() as s:
        sid = (await create_story(s, title="S")).id

    captured = {}

    async def fake_execute_image(*, final_prompt, ref_files, scene_slug, **kw):
        captured["ref_files"] = list(ref_files)
        return ExecResult(output_path="storage/images/x.png", api_call="edit", ref_files_sent=[])

    monkeypatch.setattr("app.imaging.draw_service.execute_image", fake_execute_image)
    bundle = DraftBundle(
        scene_slug="room",
        draft=IllustratorDraft(kind="new_scene", prompt_text="p", reference_manifest=[]),
        resolved=ResolvedRefs(asset_ids=[7], image_paths=[], files=[Path("/agent/a7.png")]),
        history=[],
    )
    async with Session() as s:
        await apply_decision(s, decision="confirm", bundle=bundle, final_prompt="p",
                             story_id=sid, origin="user_initiated")  # 不传 resolved
    assert captured["ref_files"] == [Path("/agent/a7.png")]
    async with Session() as s:
        ig = (await s.execute(select(ImageGen).where(ImageGen.story_id == sid))).scalar_one()
    assert json.loads(ig.ref_asset_ids) == [7]
