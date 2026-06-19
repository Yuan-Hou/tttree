"""故事导出(只读、单文件 HTML)。

GET /story/{id}/export —— 把对话流 + 场景地图打包成一个自包含的 .html 下载:
剔除所有创作要素(输入框 / 工作台 / 设置 / 绘图台 / 此刻 / 场景与图),只留浏览。
做法:读取前端「查看器」单文件构建模板(frontend/dist-viewer/viewer.html,JS/CSS 已内联),
把当前故事的冻结快照(snapshot + scene-map)注入 window.__VORE_EXPORT__,图片就地重压成
webp(最长边 1280)并内联为 data: URI。查看器复用实时应用的同款 ReadingColumn / SceneMap 组件,
故这两部分将来新增的浏览能力会自动出现在导出版里。

纯新增、纯只读:不触碰任何写路径、不改三段式 / 缓存 / 绘图 / 设置。
"""

import base64
import io
import json
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Story
from app.storage import abs_from_rel
from app.web.deps import get_session
from app.web.scene_map_router import get_scene_map
from app.web.turn_router import get_snapshot

router = APIRouter(prefix="/story", tags=["export"])

# 查看器单文件模板(由 `npm run build:viewer` 产出)。缺失时给出可操作的提示。
_VIEWER_TEMPLATE = Path(__file__).resolve().parents[3] / "frontend" / "dist-viewer" / "viewer.html"

_MAX_EDGE = 1280  # 图片最长边上限(像素)
_WEBP_QUALITY = 82


def _to_data_uri(rel: str, cache: dict[str, str]) -> str | None:
    """把一张入库相对路径的图重压成 webp(最长边≤_MAX_EDGE)并编码为 data: URI。
    同一路径只处理一次(cache 去重)。文件缺失/解码失败 → None(调用方保留原路径,优雅降级)。"""
    if rel in cache:
        return cache[rel]
    path = abs_from_rel(rel)
    if not path.is_file():
        return None
    try:
        with Image.open(path) as im:
            if im.mode == "P":
                im = im.convert("RGBA")
            elif im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            im.thumbnail((_MAX_EDGE, _MAX_EDGE))  # 仅缩小、保持纵横比
            buf = io.BytesIO()
            im.save(buf, format="WEBP", quality=_WEBP_QUALITY, method=6)
    except (OSError, ValueError):
        return None
    uri = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    cache[rel] = uri
    return uri


def _inline_images(obj: object, cache: dict[str, str]) -> object:
    """递归把数据结构里所有「storage/…」图片路径替换成内联 data: URI。
    通用遍历 → 将来快照/地图新增的图片字段无需改这里就会一并内联。"""
    if isinstance(obj, dict):
        return {k: _inline_images(v, cache) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_inline_images(v, cache) for v in obj]
    if isinstance(obj, str) and obj.startswith("storage/"):
        return _to_data_uri(obj, cache) or obj
    return obj


def _safe_filename(title: str) -> str:
    keep = "".join(c for c in title if c not in '/\\:*?"<>|\n\r\t').strip()
    return (keep or "story")[:80]


def _inject(template: str, payload: dict) -> str:
    """把冻结快照注入模板。用经典内联 <script>(在 type=module 脚本之前执行),
    JSON 里的 `<` 转义为 \\u003c,避免 </script> 截断与 HTML 解析问题。"""
    data = json.dumps(payload, ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace(chr(0x2028), "\\u2028").replace(chr(0x2029), "\\u2029")
    tag = f"<script>window.__VORE_EXPORT__={data}</script>"
    # 注入点须同时满足两点:
    #  1) 排在(被内联的)应用 type=module 脚本之前 —— 组件取数时 window.__VORE_EXPORT__ 必已就位。
    #  2) 排在 <meta charset> 之后 —— 这段内联 JSON 内含整卷故事 + 全部 base64 图,体积达数 MB;
    #     若插在 charset 声明之前,会把它顶出文档前 1024 字节。手机端以 file:// 打开(无 HTTP 头)
    #     只嗅探前 1024 字节,找不到编码声明 → 回退本地默认编码 → 中文乱码。故紧贴 charset 之后注入。
    m = re.search(r"<meta[^>]*charset[^>]*>", template, re.IGNORECASE)
    if m:
        return template[: m.end()] + tag + template[m.end() :]
    if "<head>" in template:
        return template.replace("<head>", "<head>" + tag, 1)
    if "</head>" in template:
        return template.replace("</head>", tag + "</head>", 1)
    return template.replace("<body>", "<body>" + tag, 1)


class ExportReq(BaseModel):
    # 地图节点布局(前端 localStorage 里用户整理好的坐标,按 slug)。随导出带上,导出版首开即按此落位。
    layout: dict[str, dict] = Field(default_factory=dict)


@router.post("/{story_id}/export")
async def export_story(
    story_id: str,
    req: ExportReq | None = None,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if await session.get(Story, story_id) is None:
        raise HTTPException(404, "story not found")
    if not _VIEWER_TEMPLATE.is_file():
        raise HTTPException(503, "查看器模板未构建:请在 frontend/ 下运行 `npm run build:viewer`")

    scene_map = await get_scene_map(story_id, session)
    snapshot = await get_snapshot(story_id)
    # 手动草稿图既不进黑板也不在阅读流/地图展示 → 不内联,避免无谓增大单文件。
    snapshot["scenes_drafts"] = {}

    cache: dict[str, str] = {}
    snapshot = _inline_images(snapshot, cache)
    scene_map = _inline_images(scene_map, cache)

    layout = req.layout if req else {}
    html = _inject(
        _VIEWER_TEMPLATE.read_text(encoding="utf-8"),
        {"snapshot": snapshot, "sceneMap": scene_map, "layout": layout},
    )
    filename = _safe_filename(str(snapshot.get("title") or story_id)) + ".html"
    return HTMLResponse(
        html,
        headers={
            "Content-Disposition": f"attachment; filename=\"story.html\"; filename*=UTF-8''{quote(filename)}",
        },
    )
