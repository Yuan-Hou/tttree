from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.session import create_all, engine
from app.storage import ensure_dirs
from app.web.stories_router import router as stories_router
from app.web.turn_router import router as turn_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all(engine)
    ensure_dirs()
    yield


app = FastAPI(title="Vore Tree Backend", version="0.4.0", lifespan=lifespan)
app.include_router(stories_router)
app.include_router(turn_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
