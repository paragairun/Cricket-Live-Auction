"""Database engine, session factory and the SQLite-only dev schema helper.

Version: 2.0.0 (2026-11-20)
Changed from 1.x: ensure_schema() used to run `Base.metadata.create_all` on
EVERY startup in EVERY environment, including against production PostgreSQL.
Only the column backfill below it was SQLite-gated. That created tables outside
Alembic's control and would eventually collide with a migration. The whole
function is now a no-op on any non-SQLite database.

Also adds pool_pre_ping so a connection dropped by the database (idle timeout,
failover) is detected and replaced rather than surfacing as a failed bid.
"""
import logging

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

_is_sqlite = "sqlite" in str(settings.DATABASE_URL)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    # SQLite's aiosqlite driver does not use a server-side pool.
    **({} if _is_sqlite else {"pool_size": 10, "max_overflow": 20, "pool_recycle": 1800}),
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _sqlite_type(col) -> str:
    """Map a SQLAlchemy column type to a SQLite-compatible DDL fragment."""
    from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text

    col_type = col.type
    if isinstance(col_type, Boolean):
        return "INTEGER"
    if isinstance(col_type, Integer):
        return "INTEGER"
    if isinstance(col_type, (String, Text, Enum)):
        return "TEXT"
    if isinstance(col_type, DateTime):
        return "TIMESTAMP"
    return "TEXT"


async def _backfill_sqlite_columns(conn) -> None:
    """Add columns the models define that an existing SQLite file lacks."""
    for table in Base.metadata.tables.values():
        exists = await conn.run_sync(
            lambda sync_conn, t=table: sa.inspect(sync_conn).has_table(t.name)
        )
        if not exists:
            continue

        db_cols = await conn.run_sync(
            lambda sync_conn, t=table: {
                getattr(c, "name", None)
                for c in sa.inspect(sync_conn).get_columns(t.name)
            }
        )
        db_cols = {c for c in (db_cols or set()) if c is not None}

        for col in table.columns:
            if col.name in db_cols:
                continue
            try:
                await conn.execute(
                    text(
                        f'ALTER TABLE {table.name} ADD COLUMN {col.name} {_sqlite_type(col)}'
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("SQLite backfill skipped %s.%s: %s", table.name, col.name, exc)


async def ensure_schema() -> None:
    """Create/patch the local SQLite schema. No-op on PostgreSQL."""
    if not _is_sqlite:
        # PostgreSQL and friends: the schema is owned by Alembic.
        # `alembic upgrade head` runs via the compose `migrator` service.
        logger.info("Non-SQLite database detected — schema is managed by Alembic.")
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _backfill_sqlite_columns(conn)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
