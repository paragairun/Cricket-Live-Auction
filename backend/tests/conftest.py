"""Pytest configuration and shared fixtures for the Cricket Auction Platform.

Tests use a temporary SQLite database so each test gets a clean slate.
Redis-dependent code falls back to fakeredis automatically (no Redis server needed).
"""

import asyncio
import os
import tempfile
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

# Force SQLite for tests (override any .env DATABASE_URL)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from app.core.database import AsyncSessionLocal, engine, Base, ensure_schema  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.core.queue import enqueue  # noqa: E402
from app.main import app  # noqa: E402


# ── Session-scoped event loop ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Database setup / teardown ──────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function", autouse=True)
async def db_setup():
    """Create all tables before each test, drop them after."""
    await ensure_schema()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── DB session ─────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSessionLocal, None]:
    async with AsyncSessionLocal() as session:
        yield session


# ── HTTP test client ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ── Helpers ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def make_user(db):
    """Factory to create a user directly in the DB."""

    async def _make(
        email: str = "test@example.com",
        name: str = "Test User",
        password: str = "password123",
        roles: list[str] | None = None,
    ):
        from app.models.user import User, ROLE_PLAYER

        user = User(
            name=name,
            email=email,
            hashed_password=hash_password(password),
            roles=roles or [ROLE_PLAYER],
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    return _make


@pytest_asyncio.fixture
async def make_event(db, make_user):
    """Factory to create an event owned by an organizer."""

    async def _make(
        name: str = "Test Event",
        organizer_email: str = "org@example.com",
        organizer_name: str = "Organizer",
        status: str = "draft",
        player_base_price: int = 1000,
        no_timer: bool = False,
    ):
        from app.models.auction import AuctionEvent, AuctionStatus
        from app.models.user import ROLE_ORGANIZER, ROLE_PLAYER

        org = await make_user(email=organizer_email, name=organizer_name, roles=[ROLE_PLAYER, ROLE_ORGANIZER])
        event = AuctionEvent(
            name=name,
            admin_id=org.id,
            organizer_id=org.id,
            status=AuctionStatus(status),
            player_base_price=player_base_price,
            no_timer=no_timer,
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        return event, org

    return _make


@pytest_asyncio.fixture
async def make_player(db, make_user):
    """Factory to create a player user and add them to an event."""

    async def _make(
        event_id: int,
        email: str = "player@example.com",
        name: str = "Player One",
        status: str = "pending",
    ):
        from app.models.auction import AuctionPlayer, PlayerAuctionStatus
        from app.models.user import ROLE_PLAYER

        player = await make_user(email=email, name=name, roles=[ROLE_PLAYER])
        ap = AuctionPlayer(
            event_id=event_id,
            player_id=player.id,
            base_price=1000,
            status=PlayerAuctionStatus(status),
        )
        db.add(ap)
        await db.commit()
        await db.refresh(ap)
        return player, ap

    return _make


@pytest_asyncio.fixture
async def auth_token(client, make_user):
    """Register + login a user, return the JWT access token."""

    async def _get(email: str = "test@example.com", password: str = "password123"):
        # Register
        await client.post(
            "/api/auth/register",
            json={"name": "Test User", "email": email, "password": password},
        )
        # Login
        resp = await client.post("/api/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200, resp.text
        return resp.json()["access_token"]

    return _get
