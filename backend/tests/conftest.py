"""Test fixtures.

Version: 2.0.0 (2026-11-20)

Rebuilt for the new model: players are Player rows (no account, no email), team
captains are players, and bids key on team_id. Every test runs against a fresh
in-memory-backed SQLite file and a fakeredis instance.
"""
import os

# Must be set before app.core.config is imported anywhere.
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("ALLOW_FAKE_REDIS", "true")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-chars-long")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_auction.db")

import asyncio
import pathlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import redis as redis_module
from app.core.database import AsyncSessionLocal, Base, engine
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.auction import (
    AuctionEvent,
    AuctionPlayer,
    AuctionStatus,
    BidSlab,
    Player,
    Team,
)
from app.models.user import ROLE_ADMIN, ROLE_AUCTIONEER, ROLE_ORGANIZER, ROLE_VIEWER, User

DB_FILE = pathlib.Path("./test_auction.db")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def _clean_schema():
    """Fresh schema and fresh fakeredis for every test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Reset the cached Redis client so each test gets an empty store.
    await redis_module.close_redis()
    yield
    await redis_module.close_redis()


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
def make_user():
    async def _make(
        email: str = "user@example.com",
        name: str = "Test User",
        roles: list[str] | None = None,
        password: str = "password123",
    ) -> User:
        async with AsyncSessionLocal() as session:
            user = User(
                name=name,
                email=email.lower(),
                hashed_password=hash_password(password),
                roles=roles or [ROLE_VIEWER],
                onboarded=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    return _make


@pytest_asyncio.fixture
def token_for():
    def _token(user: User) -> str:
        return create_access_token(str(user.id))

    return _token


@pytest_asyncio.fixture
def auth_headers(token_for):
    def _headers(user: User) -> dict:
        return {"Authorization": f"Bearer {token_for(user)}"}

    return _headers


@pytest_asyncio.fixture
def make_player():
    async def _make(name: str = "Player", **kwargs) -> Player:
        async with AsyncSessionLocal() as session:
            player = Player(name=name, **kwargs)
            session.add(player)
            await session.commit()
            await session.refresh(player)
            return player

    return _make


@pytest_asyncio.fixture
def make_event(make_user):
    async def _make(
        name: str = "Test League",
        status: AuctionStatus = AuctionStatus.draft,
        team_budget: int = 100000,
        team_max_players: int = 11,
        team_min_players: int = 0,
        player_base_price: int = 1000,
        no_timer: bool = True,
        admin: User | None = None,
        organizer: User | None = None,
        auctioneer: User | None = None,
    ):
        admin = admin or await make_user(email="admin@example.com", name="Admin", roles=[ROLE_ADMIN])
        organizer = organizer or await make_user(
            email="org@example.com", name="Organizer", roles=[ROLE_ORGANIZER]
        )
        auctioneer = auctioneer or await make_user(
            email="auctioneer@example.com", name="Auctioneer", roles=[ROLE_AUCTIONEER]
        )

        async with AsyncSessionLocal() as session:
            event = AuctionEvent(
                name=name,
                admin_id=admin.id,
                organizer_id=organizer.id,
                auctioneer_id=auctioneer.id,
                status=status,
                team_budget=team_budget,
                team_max_players=team_max_players,
                team_min_players=team_min_players,
                player_base_price=player_base_price,
                no_timer=no_timer,
                allowed_domains=[],
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return event, admin, organizer, auctioneer

    return _make


@pytest_asyncio.fixture
def make_team():
    async def _make(
        event: AuctionEvent,
        name: str = "Team A",
        budget: int | None = None,
        max_players: int = 11,
        captain_id: int | None = None,
    ) -> Team:
        async with AsyncSessionLocal() as session:
            team = Team(
                event_id=event.id,
                name=name,
                budget=budget if budget is not None else event.team_budget,
                max_players=max_players,
                captain_id=captain_id,
            )
            session.add(team)
            await session.commit()
            await session.refresh(team)
            return team

    return _make


@pytest_asyncio.fixture
def register_player(make_player):
    """Create a Player and register them into an event."""

    async def _register(
        event: AuctionEvent, name: str = "Player", base_price: int = 1000, **kwargs
    ) -> tuple[Player, AuctionPlayer]:
        player = await make_player(name=name, **kwargs)
        async with AsyncSessionLocal() as session:
            ap = AuctionPlayer(
                event_id=event.id, player_id=player.id, base_price=base_price
            )
            session.add(ap)
            await session.commit()
            await session.refresh(ap)
            return player, ap

    return _register


@pytest_asyncio.fixture
def make_slab():
    async def _make(
        event: AuctionEvent, min_value: int, max_value: int, increment: int, sort_order: int = 0
    ) -> BidSlab:
        async with AsyncSessionLocal() as session:
            slab = BidSlab(
                event_id=event.id,
                min_value=min_value,
                max_value=max_value,
                increment=increment,
                sort_order=sort_order,
            )
            session.add(slab)
            await session.commit()
            await session.refresh(slab)
            return slab

    return _make


def pytest_sessionfinish(session, exitstatus):
    if DB_FILE.exists():
        try:
            DB_FILE.unlink()
        except OSError:
            pass
