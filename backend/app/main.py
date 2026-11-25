"""FastAPI application entrypoint.

Version: 2.0.0 (2026-11-20)
Changed from 1.x:
  - CORS no longer uses allow_origins=["*"] with allow_credentials=True. That
    pairing is invalid per the CORS spec (browsers reject it) and was far more
    permissive than anything this app needs. Origins now come from settings.
  - ensure_schema() no longer runs create_all against PostgreSQL. Previously it
    ran on every startup in every environment, creating tables outside Alembic's
    control; only the column backfill was SQLite-gated, not the create_all.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal, ensure_schema
from app.core.background import shutdown_background_tasks
from app.services.auction_timer import timer
from app.models import *  # noqa: F401,F403 - register all models
from app.api import auth, admin, organizer, auction, events
from app.ws.manager import manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    await ensure_schema()
    await manager.startup()
    await timer.startup()
    logger.info(
        "Cricket Auction API started (environment=%s, timer=%ss, soft_close=%ss)",
        settings.ENVIRONMENT,
        settings.AUCTION_TIMER_SECONDS,
        settings.SOFT_CLOSE_EXTENSION_SECONDS,
    )
    yield
    await timer.shutdown()
    await manager.shutdown()
    await shutdown_background_tasks()


app = FastAPI(
    title="Cricket Auction API",
    version="2.0.0",
    lifespan=lifespan,
    # Interactive docs are a convenience, not something to expose in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(organizer.router, prefix="/api")
app.include_router(auction.router, prefix="/api")
app.include_router(events.router, prefix="/api")

# Serve uploaded files
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


@app.get("/api/health")
async def health():
    """Report database reachability and local timer status."""
    db_ok = False
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        logger.error("Health check: database unreachable: %s", exc)
    healthy = db_ok
    return {
        "status": "ok" if healthy else "degraded",
        "environment": settings.ENVIRONMENT,
        "database": "ok" if db_ok else "unreachable",
        "auction_timer": "running" if timer._task and not timer._task.done() else "stopped",
        "realtime": "in_process",
    }
