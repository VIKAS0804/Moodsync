"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.clients.storage import PreviewCache
from app.config import Settings, get_settings
from app.db import engine, init_db
from app.deps import settings_dep
from app.routers import auth, mood, sync
from app.schemas import HealthResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("moodsync")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        log.info("database ready")
    except Exception:  # noqa: BLE001 - the API should still boot to serve /health
        log.exception("database init failed; check DATABASE_URL")
    yield


app = FastAPI(
    title="MoodSync API",
    version="0.1.0",
    description=(
        "Mood-adaptive music backend. A 1-100 slider position maps to a track "
        "from the user's own Spotify library, scored by a local DSP pipeline "
        "over Apple Music preview clips."
    ),
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(sync.router)
app.include_router(mood.router)


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health(settings: Settings = Depends(settings_dep)) -> HealthResponse:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001
        database = f"unavailable: {type(exc).__name__}"

    return HealthResponse(
        status="ok",
        environment=settings.environment,
        database=database,
        apple_music_configured=settings.apple_music_configured,
        spotify_configured=bool(settings.spotify_client_id),
        preview_cache=PreviewCache(settings).backend,
    )
