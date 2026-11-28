"""Tests for upgrading an existing SQLite database in place.

New in 2.1.1 (2026-11-20).

These cover the failure that took a running local install down after the v2.1
upgrade. `ensure_schema()`'s backfill added new columns with a bare
`ALTER TABLE t ADD COLUMN c TYPE` and no DEFAULT, so rows already in the table
got NULL in columns the models declare non-nullable with a default. Reading one
back failed Pydantic validation, FastAPI returned 500, and — because an
unhandled error response carries no CORS headers — the browser reported it as a
network error, which looks identical to the backend being down.

The second test is the one that matters for anyone already affected: a database
damaged by the old function must heal itself on the next startup, not stay
broken.
"""
import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import database as db_module
from app.models.auction import AuctionEvent
from app.schemas.auction import AuctionEventOut

# A pre-2.1 auction_events table, as the old create_all would have left it:
# no auction_mode, rtm_enabled, rtm_cards_per_team or team_min_players.
LEGACY_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, phone TEXT,
    hashed_password TEXT, roles TEXT, profile_photo TEXT,
    is_active BOOLEAN DEFAULT 1, onboarded BOOLEAN DEFAULT 0,
    created_at TIMESTAMP, deleted_at TIMESTAMP
);
CREATE TABLE auction_events (
    id INTEGER PRIMARY KEY, name TEXT, description TEXT,
    admin_id INTEGER, organizer_id INTEGER, auctioneer_id INTEGER,
    status TEXT, allowed_domains TEXT, scheduled_at TIMESTAMP,
    team_budget INTEGER, team_max_players INTEGER, player_base_price INTEGER,
    no_timer BOOLEAN, total_viewers INTEGER, logo TEXT, created_at TIMESTAMP
);
INSERT INTO users (id,name,email,hashed_password,roles,is_active,onboarded,created_at)
  VALUES (1,'Admin','legacy-admin@example.com','h','["admin"]',1,1,'2026-01-01 00:00:00');
INSERT INTO auction_events
  (id,name,admin_id,organizer_id,auctioneer_id,status,allowed_domains,
   team_budget,team_max_players,player_base_price,no_timer,created_at)
  VALUES (1,'Legacy League',1,1,1,'draft','[]',100000,15,1000,0,'2026-01-01 00:00:00');
"""


def _build_legacy_db(path, *, add_null_columns: bool) -> None:
    con = sqlite3.connect(path)
    con.executescript(LEGACY_SCHEMA)
    if add_null_columns:
        # Exactly what the pre-2.1.1 backfill did: add the column, no default,
        # leaving the existing row NULL.
        for column, sql_type in (
            ("auction_mode", "TEXT"),
            ("rtm_enabled", "INTEGER"),
            ("rtm_cards_per_team", "INTEGER"),
            ("team_min_players", "INTEGER"),
            # Added by the event-profile feature; legacy SQLite files need the
            # same empty-object default as fresh databases.
            ("event_details", "TEXT"),
        ):
            con.execute(f"ALTER TABLE auction_events ADD COLUMN {column} {sql_type}")
    con.commit()
    con.close()


async def _upgrade_and_read(tmp_path, *, add_null_columns: bool) -> AuctionEvent:
    """Run ensure_schema() against a legacy database and read the row back."""
    db_path = tmp_path / "legacy.db"
    _build_legacy_db(db_path, add_null_columns=add_null_columns)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # ensure_schema() works off the module-level engine, so point it here.
    original_engine, original_sqlite = db_module.engine, db_module._is_sqlite
    db_module.engine, db_module._is_sqlite = engine, True
    try:
        await db_module.ensure_schema()
        async with session_factory() as session:
            row = (
                await session.execute(select(AuctionEvent).where(AuctionEvent.id == 1))
            ).scalar_one()
            return row
    finally:
        db_module.engine, db_module._is_sqlite = original_engine, original_sqlite
        await engine.dispose()


@pytest.mark.parametrize(
    "add_null_columns, label",
    [
        (False, "columns missing entirely"),
        (True, "columns already present but NULL (damaged by the old backfill)"),
    ],
)
async def test_legacy_database_upgrades_to_a_readable_row(
    tmp_path, add_null_columns, label
):
    row = await _upgrade_and_read(tmp_path, add_null_columns=add_null_columns)

    # The defaults the models declare must actually be on the row.
    assert row.team_min_players == 0, label
    assert row.rtm_cards_per_team == 0, label
    assert row.rtm_enabled is False, label
    assert row.auction_mode is not None, label
    assert getattr(row.auction_mode, "value", row.auction_mode) == "random", label
    assert row.event_details == {}, label

    # And the row must serialise — this is what GET /auction/events/{id} does,
    # and what used to raise and surface as a 500.
    out = AuctionEventOut.model_validate(row)
    assert out.id == 1
    assert out.name == "Legacy League"
    assert out.status == "draft", "A draft event must still read as draft"


async def test_existing_values_are_not_overwritten(tmp_path):
    """The repair pass must only touch NULLs, never real data."""
    db_path = tmp_path / "legacy.db"
    _build_legacy_db(db_path, add_null_columns=True)

    con = sqlite3.connect(db_path)
    con.execute(
        "UPDATE auction_events SET team_min_players = 7, rtm_enabled = 1, "
        "event_details = '{\"tagline\": \"Keep this profile\"}'"
    )
    con.commit()
    con.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    original_engine, original_sqlite = db_module.engine, db_module._is_sqlite
    db_module.engine, db_module._is_sqlite = engine, True
    try:
        await db_module.ensure_schema()
        async with session_factory() as session:
            row = (
                await session.execute(select(AuctionEvent).where(AuctionEvent.id == 1))
            ).scalar_one()
    finally:
        db_module.engine, db_module._is_sqlite = original_engine, original_sqlite
        await engine.dispose()

    assert row.team_min_players == 7, "A configured value must survive the repair"
    assert row.rtm_enabled is True, "A configured value must survive the repair"
    assert row.event_details == {"tagline": "Keep this profile"}, (
        "An existing event profile must survive the repair"
    )
