"""阶段③:执行层。把(用户已确认的)提示词 + 解析后的参考图,翻译成绘图模型调用。

可被异步调用的纯执行函数;按故事所选绘图模型分流到两条出图 API:
- gpt-image-2(OpenAI images,generate/edit);
- gemini-*-image(Google 原生 generateContent,参考图作 inline 图块)。
两条都经接入点层(app.llm.endpoints)取 base_url + key(本站/自定义)。对用户/对模型全程用语义名;
只有这里,才把语义名按引用清单映射成实际文件路径。
"""

import base64
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.imaging.image_models import get_image_model
from app.llm.endpoints import resolve_endpoint
from app.llm.openai_client import get_openai_client
from app.models.schemas import ReferenceRef
from app.storage import BACKEND_ROOT, IMAGES_SUBDIR

DEFAULT_SIZE = "1536x1024"  # 横构图,宽高均被 16 整除


class ImageGenError(Exception):
    """绘图模型调用失败(含内容审核拒绝/参数错误/网络等)。执行层捕获后抛出,
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
    image_model: str | None = None,
) -> ExecResult:
    """按故事所选绘图模型出图。有参考图 → 变体/编辑(传参考图);无 → 文生图。
    失败抛 ImageGenError,不崩溃。确认闸门在上层,本函数不自带旁路。"""
    im = get_image_model(image_model)
    (base_dir / IMAGES_SUBDIR).mkdir(parents=True, exist_ok=True)
    out_rel = f"{IMAGES_SUBDIR}/{scene_slug}_{uuid.uuid4().hex[:8]}.png"
    use_edit = bool(ref_files)

    if im.api == "gemini":
        png = await _gemini_generate(im, final_prompt=final_prompt, ref_files=ref_files)
    else:
        png = await _openai_generate(
            im, final_prompt=final_prompt, ref_files=ref_files, size=size, use_edit=use_edit
        )

    (base_dir / out_rel).write_bytes(png)
    return ExecResult(
        output_path=out_rel,
        api_call="edit" if use_edit else "generate",
        ref_files_sent=[str(Path(p)) for p in ref_files],
    )


async def _openai_generate(im, *, final_prompt, ref_files, size, use_edit) -> bytes:
    """gpt-image-2:images.edit(有参考图)/ images.generate(文生图)。不传 input_fidelity。"""
    client = get_openai_client()  # 走 openai 接入点(本站 .env / 全局设置自定义)
    try:
        if use_edit:
            handles = [Path(p).open("rb") for p in ref_files]
            try:
                resp = await client.images.edit(
                    model=im.model, image=handles, prompt=final_prompt, size=size
                )
            finally:
                for h in handles:
                    h.close()
        else:
            resp = await client.images.generate(model=im.model, prompt=final_prompt, size=size)
    except Exception as exc:  # 内容审核拒绝(BadRequest)/网络/参数等,统一兜住不崩溃
        raise ImageGenError(f"{type(exc).__name__}: {exc}") from exc

    b64 = resp.data[0].b64_json if resp.data else None
    if not b64:
        raise ImageGenError("API 未返回图像数据(可能被内容策略拦截)")
    return base64.b64decode(b64)


_GEMINI_TIMEOUT = 180.0  # 出图慢,给足


async def _gemini_generate(im, *, final_prompt, ref_files) -> bytes:
    """Gemini 原生 generateContent 出图。参考图(若有)作 inlineData 图块一并送入,实现编辑/变体。

    经 google_image 接入点取 base_url + key(本站 .env / 全局设置自定义)。请求声明
    responseModalities=["IMAGE"];响应里取第一块 inlineData 的 base64 图。失败统一抛 ImageGenError。
    """
    import httpx

    base_url, key = resolve_endpoint(im.endpoint_id)
    if not key:
        raise ImageGenError(
            f"接入点 {im.endpoint_id!r} 无可用 key:本站点服务的 new-api 模型 key 尚未就绪"
            "(重新登录可自动补齐),或在全局设置切到「自定义」填自己的 key"
        )
    url = base_url.rstrip("/") + f"/models/{im.model}:generateContent"

    parts: list[dict] = [{"text": final_prompt}]
    for p in ref_files:
        raw = Path(p).read_bytes()
        mime = "image/png" if str(p).lower().endswith(".png") else "image/jpeg"
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(raw).decode()}})
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }

    try:
        async with httpx.AsyncClient(timeout=_GEMINI_TIMEOUT) as client:
            resp = await client.post(url, headers={"x-goog-api-key": key}, json=body)
    except Exception as exc:  # 网络/超时等
        raise ImageGenError(f"{type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise ImageGenError(f"Gemini 出图 HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    for cand in data.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    raise ImageGenError("Gemini 未返回图像数据(可能被内容策略拦截或模型不支持出图)")
