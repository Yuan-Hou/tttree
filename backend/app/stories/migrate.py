"""故事迁移包(parquet bundle):把一卷故事的全部数据导出成单个 .zip,可导入到另一个账号
或另一个部署实例,完整重建。

为什么是「.zip 内含多个 parquet」:故事数据横跨 8 张表(stories / blackboard / knowledge /
turns / reference_assets / image_gens / story_settings / draw_proposals),parquet 天然按表存,
故每表一个 .parquet;图片是花钱生成的真实资产,跨部署必须随包带上 → 另存 blobs.parquet
(rel_path → 字节)。账号级表(users / app_settings / newapi_accounts)绝不入包——导入端
用目标账号自己的设置。

导入端的 ID 重映射与 store.fork_story 同源(连 ref_asset_ids / done_image_id / draft_manifest
都按同一套规则重写),区别只是数据从 parquet 行而非 ORM 行读出,且额外把图片二进制落回磁盘。
图片相对路径**原样保留**(文件名是 uuid/hash,跨实例不撞),故黑板 / ref_image_paths 里那些
路径引用无需重写;只把字节写回同一相对路径即可。
"""

import io
import json
import uuid
import zipfile
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import Boolean, DateTime, Integer, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Blackboard,
    DrawProposal,
    ImageGen,
    Knowledge,
    ReferenceAsset,
    Story,
    StorySettings,
    Turn,
    _utcnow,
)
from app.storage import BACKEND_ROOT, abs_from_rel
from app.stories.store import _remap_manifest_assets

BUNDLE_VERSION = 1

# 故事级表的导出顺序(stories 在前;其余按 story_id 归属)。每项 = (成员名, 模型)。
_TABLES: list[tuple[str, type]] = [
    ("stories", Story),
    ("blackboard", Blackboard),
    ("knowledge", Knowledge),
    ("story_settings", StorySettings),
    ("turns", Turn),
    ("reference_assets", ReferenceAsset),
    ("image_gens", ImageGen),
    ("draw_proposals", DrawProposal),
]


def _pa_type(coltype) -> pa.DataType:
    """SQLAlchemy 列类型 → pyarrow 类型。显式给类型(而非让 pyarrow 推断),保证全 None 的列
    也能稳定往返,且跨 pyarrow 版本一致。"""
    if isinstance(coltype, Boolean):
        return pa.bool_()
    if isinstance(coltype, Integer):
        return pa.int64()
    if isinstance(coltype, DateTime):
        return pa.timestamp("us")
    return pa.string()  # String / Text


def _rows_to_parquet(rows: list, model: type) -> bytes:
    cols = list(model.__table__.columns)
    arrays = {
        c.name: pa.array([getattr(r, c.name) for r in rows], type=_pa_type(c.type)) for c in cols
    }
    sink = io.BytesIO()
    pq.write_table(pa.table(arrays), sink)
    return sink.getvalue()


def _parquet_to_rows(data: bytes) -> list[dict]:
    return pq.read_table(io.BytesIO(data)).to_pylist()


def _safe_name(title: str) -> str:
    keep = "".join(c for c in (title or "") if c not in '/\\:*?"<>|\n\r\t').strip()
    return (keep or "story")[:80]


# ── 导出 ──────────────────────────────────────────────────────────────────────


async def export_bundle(
    session: AsyncSession, story_id: str, *, base_dir=BACKEND_ROOT
) -> tuple[bytes, str]:
    """读出整故事 → 打包成 .zip 字节。返回 (zip 字节, 建议文件名)。源不存在抛 KeyError。"""
    story = await session.get(Story, story_id)
    if story is None:
        raise KeyError(story_id)

    # 各表行(stories 取自身;其余按 story_id)
    table_rows: dict[str, list] = {"stories": [story]}
    for name, model in _TABLES[1:]:
        col = model.__table__.columns["story_id"]
        rows = (await session.execute(select(model).where(col == story_id))).scalars().all()
        table_rows[name] = list(rows)

    # 图片二进制:image_gens.output_path + reference_assets.file_path,去重后读字节
    rels: list[str] = []
    seen: set[str] = set()
    for ig in table_rows["image_gens"]:
        for rel in (ig.output_path, *json.loads(ig.ref_image_paths or "[]")):
            if isinstance(rel, str) and rel.startswith("storage/") and rel not in seen:
                seen.add(rel)
                rels.append(rel)
    for ra in table_rows["reference_assets"]:
        if ra.file_path and ra.file_path.startswith("storage/") and ra.file_path not in seen:
            seen.add(ra.file_path)
            rels.append(ra.file_path)
    blobs: list[tuple[str, bytes]] = []
    for rel in rels:
        p = abs_from_rel(rel, base_dir)
        if p.is_file():
            blobs.append((rel, p.read_bytes()))  # 缺失文件直接跳过(优雅降级)

    manifest = {
        "version": BUNDLE_VERSION,
        "kind": "vore-tree-story-bundle",
        "story_id": story_id,
        "title": story.title,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "tables": {name: len(rows) for name, rows in table_rows.items()},
        "blob_count": len(blobs),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, model in _TABLES:
            z.writestr(f"tables/{name}.parquet", _rows_to_parquet(table_rows[name], model))
        z.writestr("blobs.parquet", _blobs_to_parquet(blobs))
    return buf.getvalue(), _safe_name(story.title) + ".vtree.zip"


def _blobs_to_parquet(blobs: list[tuple[str, bytes]]) -> bytes:
    table = pa.table(
        {
            "rel_path": pa.array([r for r, _ in blobs], pa.string()),
            "data": pa.array([d for _, d in blobs], pa.binary()),
        }
    )
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


# ── 导入 ──────────────────────────────────────────────────────────────────────


def _kw(row: dict, model: type, drop: tuple[str, ...] = (), **override) -> dict:
    """从 parquet 行字典构造 ORM kwargs:取该模型有的列,去掉 drop,叠加 override。"""
    cols = {c.name for c in model.__table__.columns}
    out = {k: v for k, v in row.items() if k in cols and k not in drop}
    out.update(override)
    return out


async def import_bundle(
    session: AsyncSession, owner_id: str, data: bytes, *, base_dir=BACKEND_ROOT
) -> Story:
    """把迁移包 .zip 导入到 owner_id 名下,完整重建为一卷新故事(新 story_id + 新自增主键 +
    重映射跨表引用 + 图片字节落回磁盘)。返回新 Story。包损坏/版本过新抛 ValueError。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError("不是有效的迁移包(.zip)") from e
    with zf as z:
        names = set(z.namelist())
        if "manifest.json" not in names:
            raise ValueError("迁移包缺少 manifest.json")
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("kind") != "vore-tree-story-bundle":
            raise ValueError("不是 vore-tree 故事迁移包")
        if int(manifest.get("version", 0)) > BUNDLE_VERSION:
            raise ValueError("迁移包版本过新,请升级目标实例后再导入")

        def rows(name: str) -> list[dict]:
            member = f"tables/{name}.parquet"
            return _parquet_to_rows(z.read(member)) if member in names else []

        blob_rows = _parquet_to_rows(z.read("blobs.parquet")) if "blobs.parquet" in names else []
        # 在退出 with 前把要用的 parquet 全部读出
        story_rows = rows("stories")
        bb_rows, kb_rows, st_rows = rows("blackboard"), rows("knowledge"), rows("story_settings")
        turn_rows = rows("turns")
        ref_rows = sorted(rows("reference_assets"), key=lambda r: r["id"])
        ig_rows = sorted(rows("image_gens"), key=lambda r: r["id"])
        dp_rows = sorted(rows("draw_proposals"), key=lambda r: r["id"])

    if not story_rows:
        raise ValueError("迁移包缺少 stories 表")

    new_id = uuid.uuid4().hex
    src = story_rows[0]
    # 归属改为导入者;created_at 保留来源(完整迁移),last_active_at 置为现在 → 导入后浮到书架顶。
    session.add(Story(**_kw(src, Story, drop=("id", "owner_id", "last_active_at"),
                            id=new_id, owner_id=owner_id, last_active_at=_utcnow())))

    for r in bb_rows:
        session.add(Blackboard(**_kw(r, Blackboard, drop=("story_id",), story_id=new_id)))
    for r in kb_rows:
        session.add(Knowledge(**_kw(r, Knowledge, drop=("story_id",), story_id=new_id)))
    for r in st_rows:
        session.add(StorySettings(**_kw(r, StorySettings, drop=("story_id",), story_id=new_id)))
    for r in turn_rows:  # turns.id 自增主键:丢弃旧值让 DB 重发(无表引用它,引用走 turn_index)
        session.add(Turn(**_kw(r, Turn, drop=("id", "story_id"), story_id=new_id)))

    # 参考图:新自增 id,记 旧→新 供 ImageGen.ref_asset_ids / draft_manifest 重映射
    ref_id_map: dict[int, int] = {}
    for r in ref_rows:
        nr = ReferenceAsset(**_kw(r, ReferenceAsset, drop=("id", "story_id"), story_id=new_id))
        session.add(nr)
        await session.flush()
        ref_id_map[r["id"]] = nr.id

    # ImageGen:重映射 ref_asset_ids;记 旧→新 供 DrawProposal.done_image_id 重映射
    imagegen_id_map: dict[int, int] = {}
    for r in ig_rows:
        old_ref_ids = json.loads(r.get("ref_asset_ids") or "[]")
        new_ref_ids = [ref_id_map.get(x, x) for x in old_ref_ids]
        nig = ImageGen(**_kw(r, ImageGen, drop=("id", "story_id", "ref_asset_ids"),
                             story_id=new_id,
                             ref_asset_ids=json.dumps(new_ref_ids, ensure_ascii=False)))
        session.add(nig)
        await session.flush()
        imagegen_id_map[r["id"]] = nig.id

    for r in dp_rows:
        done = r.get("done_image_id")
        session.add(DrawProposal(**_kw(r, DrawProposal,
                                       drop=("id", "story_id", "done_image_id", "draft_manifest"),
                                       story_id=new_id,
                                       done_image_id=imagegen_id_map.get(done) if done is not None else None,
                                       draft_manifest=_remap_manifest_assets(r.get("draft_manifest") or "[]", ref_id_map))))

    # 图片字节落回磁盘(相对路径原样,故各处路径引用无需重写)。防穿越:仅 storage/ 下、无 ..
    for b in blob_rows:
        rel, raw = b.get("rel_path"), b.get("data")
        if not isinstance(rel, str) or raw is None:
            continue
        if not rel.startswith("storage/") or ".." in rel:
            continue
        p = abs_from_rel(rel, base_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)

    await session.commit()
    return await session.get(Story, new_id)
