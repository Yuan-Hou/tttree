"""阶段③:执行层。把(用户已确认的)提示词 + 解析后的参考图,翻译成 gpt-image-2 调用。

这是一个可被异步调用的纯执行函数,M4 接前端时直接复用,无需改动。
对用户/对模型全程用语义名;只有这里,才把语义名按引用清单映射成实际文件路径。
"""

import base64
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.llm.openai_client import get_openai_client
from app.models.schemas import ReferenceRef
from app.storage import BACKEND_ROOT, IMAGES_SUBDIR

DEFAULT_SIZE = "1536x1024"  # 横构图,宽高均被 16 整除


class ImageGenError(Exception):
    """gpt-image-2 调用失败(含内容审核拒绝/参数错误/网络等)。执行层捕获后抛出,
    由上层告知用户、支持重试,不崩溃。"""


@dataclass
class ResolvedRefs:
    asset_ids: list[int] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)  # 历史生成图相对路径
    files: list[Path] = field(default_factory=list)  # 实际传给 API 的文件(绝对路径)


def resolve_references(
    manifest: Sequence[ReferenceRef],
    assets_by_id: dict[int, Any],
    *,
    base_dir: Path = BACKEND_ROOT,
) -> ResolvedRefs:
    """语义名 → 实际文件。只有执行层做这步映射;前面环节全用语义名。"""
    out = ResolvedRefs()
    for r in manifest:
        if r.source == "reference_asset" and r.asset_id is not None:
            asset = assets_by_id.get(r.asset_id)
            if asset is not None:
                out.asset_ids.append(r.asset_id)
                out.files.append(base_dir / asset.file_path)
        elif r.source == "history_image" and r.image_path:
            out.image_paths.append(r.image_path)
            out.files.append(base_dir / r.image_path)
    return out


@dataclass
class ExecResult:
    output_path: str  # 相对 backend 根
    api_call: str  # "generate" | "edit"
    ref_files_sent: list[str]


async def execute_image(
    *,
    final_prompt: str,
    ref_files: Sequence[Path] = (),
    scene_slug: str = "scene",
    size: str = DEFAULT_SIZE,
    base_dir: Path = BACKEND_ROOT,
    model: str | None = None,
) -> ExecResult:
    """有参考图 → images.edit(变体,传参考图);无 → images.generate(文生图)。
    不传 input_fidelity(gpt-image-2 不允许)。失败抛 ImageGenError,不崩溃。"""
    model = model or settings.openai_image_model
    client = get_openai_client()
    (base_dir / IMAGES_SUBDIR).mkdir(parents=True, exist_ok=True)
    out_rel = f"{IMAGES_SUBDIR}/{scene_slug}_{uuid.uuid4().hex[:8]}.png"

    use_edit = bool(ref_files)
    try:
        if use_edit:
            handles = [Path(p).open("rb") for p in ref_files]
            try:
                resp = await client.images.edit(
                    model=model, image=handles, prompt=final_prompt, size=size
                )
            finally:
                for h in handles:
                    h.close()
        else:
            resp = await client.images.generate(model=model, prompt=final_prompt, size=size)
    except Exception as exc:  # 含内容审核拒绝(BadRequest)/网络/参数等,统一兜住不崩溃
        raise ImageGenError(f"{type(exc).__name__}: {exc}") from exc

    b64 = resp.data[0].b64_json if resp.data else None
    if not b64:
        raise ImageGenError("API 未返回图像数据(可能被内容策略拦截)")
    (base_dir / out_rel).write_bytes(base64.b64decode(b64))

    return ExecResult(
        output_path=out_rel,
        api_call="edit" if use_edit else "generate",
        ref_files_sent=[str(Path(p)) for p in ref_files],
    )
