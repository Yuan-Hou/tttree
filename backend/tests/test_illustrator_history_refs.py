"""绘图写稿 Agent 不再看到历史图的原始 image_path。

参考库素材给干净句柄 asset_id;历史生成图只凭语义名指代,真实路径由后端按语义名权威回填进
manifest(Agent 写错/写空都不经手它)。catalog 渲染不露路径,_backfill_history_paths 负责回填。
"""

from app.agents.illustrator import render_reference_catalog
from app.db.models import ReferenceAsset
from app.imaging.draw_service import _backfill_history_paths
from app.models.schemas import ReferenceRef


def test_catalog_hides_history_paths_but_keeps_asset_id():
    asset = ReferenceAsset(
        id=7, story_id="s", label="主角立绘", category="角色",
        description="白子立绘", file_path="storage/references/a.png",
    )
    hist = [{"semantic_name": "糖水机·初见", "image_path": "storage/images/h_abc.png", "note": "保持布局"}]
    out = render_reference_catalog([asset], history_images=hist)
    # 参考库:仍给 asset_id(干净句柄,本就不是噪声)
    assert "asset_id=7" in out and "主角立绘" in out
    # 历史图:只露语义名,绝不露原始路径
    assert "糖水机·初见" in out
    assert "storage/images/h_abc.png" not in out
    assert "image_path" not in out


def test_backfill_history_paths_authoritative_by_semantic_name():
    history = [
        {"semantic_name": "糖水机·初见", "image_path": "storage/images/t4.png", "note": ""},
        {"semantic_name": "糖水机·再访", "image_path": "storage/images/t5.png", "note": ""},
    ]
    manifest = [
        ReferenceRef(semantic_name="主角立绘", source="reference_asset", asset_id=1, purpose="角色"),
        ReferenceRef(semantic_name="糖水机·再访", source="history_image", image_path=None, purpose="布局"),
        # Agent 即便杜撰了路径,也一律以语义名为准覆盖
        ReferenceRef(semantic_name="糖水机·初见", source="history_image", image_path="瞎填的路径", purpose="布局"),
        # 语义名匹配不到 → 置空,下游(RefPicker 预选 / 出图解析)自然跳过
        ReferenceRef(semantic_name="不存在·X", source="history_image", image_path=None, purpose="布局"),
    ]
    _backfill_history_paths(manifest, history)
    assert manifest[0].asset_id == 1 and manifest[0].image_path is None  # 参考库项不动
    assert manifest[1].image_path == "storage/images/t5.png"  # 按语义名回填
    assert manifest[2].image_path == "storage/images/t4.png"  # 覆盖 Agent 杜撰的路径
    assert manifest[3].image_path is None  # 匹配不到 → 空
