from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db.session import create_all, engine
from app.storage import STORAGE_ROOT, ensure_dirs
from app.web.draw_router import router as draw_router
from app.web.references_router import router as references_router
from app.web.stories_router import router as stories_router
from app.web.turn_router import router as turn_router

_STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all(engine)
    ensure_dirs()
    yield


app = FastAPI(title="Vore Tree Backend", version="0.4.0", lifespan=lifespan)
app.include_router(stories_router)
app.include_router(turn_router)
app.include_router(draw_router)
app.include_router(references_router)

# 生成图/参考图按相对路径(storage/...)存,这里挂成静态目录供浏览器取缩略图。
app.mount("/storage", StaticFiles(directory=str(STORAGE_ROOT), check_dir=False), name="storage")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    """极简测试页(原生 JS,M4-D)。"""
    return FileResponse(_STATIC_DIR / "index.html")
