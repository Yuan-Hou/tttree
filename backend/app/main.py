from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import brand_title, settings
from app.db.session import async_session, create_all, engine
from app.global_settings_store import load_overrides_into_memory
from app.newapi.store import load_site_keys_into_memory
from app.storage import STORAGE_ROOT, ensure_dirs
from app.web.auth_router import router as auth_router
from app.web.bibles_router import router as bibles_router
from app.web.draw_router import router as draw_router
from app.web.export_router import router as export_router
from app.web.global_settings_router import router as global_settings_router
from app.web.knowledge_router import router as knowledge_router
from app.web.references_router import router as references_router
from app.web.scene_map_router import router as scene_map_router
from app.web.settings_router import router as settings_router
from app.web.stories_router import router as stories_router
from app.web.time_router import router as time_router
from app.web.turn_router import router as turn_router

_STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
# M5 前端构建产物(frontend/dist)。开发期走 Vite dev server(:5173,proxy 到此);
# 部署期 `npm run build` 后,这里把产物挂在 /app 下,与 API 同源、无需 CORS。
_SPA_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all(engine)
    ensure_dirs()
    # 全局设置:把库里自填的接入点配置载入内存覆盖表,供 registry 取用。
    # new-api:把各用户的本站点服务模型 key 载入内存(供 resolve_endpoint 本站点服务用)。
    async with async_session() as session:
        await load_overrides_into_memory(session)
        await load_site_keys_into_memory(session)
    yield


app = FastAPI(title=f"{brand_title()} Backend", version="0.4.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(stories_router)
app.include_router(turn_router)
app.include_router(draw_router)
app.include_router(references_router)
app.include_router(time_router)
app.include_router(settings_router)
app.include_router(knowledge_router)
app.include_router(bibles_router)
app.include_router(global_settings_router)
app.include_router(scene_map_router)
app.include_router(export_router)

# 生成图/参考图按相对路径(storage/...)存,这里挂成静态目录供浏览器取缩略图。
app.mount("/storage", StaticFiles(directory=str(STORAGE_ROOT), check_dir=False), name="storage")

# 有构建产物时,把 M5 前端挂在 /app(html=True → SPA 回退)。无产物(纯后端/开发期)则跳过。
if (_SPA_DIR / "index.html").exists():
    app.mount("/app", StaticFiles(directory=str(_SPA_DIR), html=True), name="spa")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/brand")
async def brand() -> dict[str, str]:
    """站点品牌名(公开,无需登录)。前端同步后本地缓存,展示「{name} Tree」(name 空 → 仅「Tree」)。"""
    return {"name": settings.site_name, "title": brand_title()}


@app.get("/")
async def index() -> FileResponse:
    """极简测试页(原生 JS,M4-D)。"""
    return FileResponse(_STATIC_DIR / "index.html")
