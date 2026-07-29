# server/app/main.py
# Guitar Trainer FastAPI application entry point.
# CORS: allow_origins=["*"] for Phase 1 POC — narrow in Phase 2 when user data lands.
# CRITICAL: allow_credentials MUST be False when allow_origins=["*"] (Pitfall 5).
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.breakdowns import router as breakdowns_router
from app.api.v1.sessions import router as sessions_router
from app.api.v1.song_of_day import router as song_router
from app.api.v1.users import router as users_router
from app.db.seed import seed_songs
from app.db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fletcher API",
    version="1.0.0",
    description="Fletcher — mobile AI guitar teacher. Walking Skeleton: song-of-day endpoint backed by Postgres.",
)

# CORS: allow all origins for mobile POC client.
# Mobile apps don't send an Origin header for native fetches;
# "*" is safe here because no credentials or PII are served in Phase 1.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # Phase 1 POC — narrow to Railway URL in Phase 2
    allow_credentials=False,   # MUST be False when allow_origins=["*"]
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(breakdowns_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(song_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")


@app.get("/healthz", tags=["ops"])
async def health() -> dict:
    """Health check endpoint used by Railway's healthcheck probe."""
    return {"status": "ok"}


@app.on_event("startup")
async def on_startup() -> None:
    """Seed the songs table on startup if it is empty."""
    logger.info("Startup: seeding songs table if empty.")
    async with AsyncSessionLocal() as db:
        await seed_songs(db)
    logger.info("Startup: seed check complete.")
