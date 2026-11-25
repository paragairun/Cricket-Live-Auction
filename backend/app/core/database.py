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
import enum
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


def _default_literal(col) -> str | None:
    """A SQL literal for this column's default, or None if there isn't a usable one.

    Only scalar defaults are usable: a callable default (a timestamp factory,
    `list`) cannot be expressed as DDL, and those columns are nullable anyway.
    """
    from sqlalchemy.sql.elements import TextClause

    value = None
    default = col.default
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
    elif col.server_default is not None:
        arg = col.server_default.arg
        if isinstance(arg, TextClause):
            text_value = str(arg.text).strip()
            return text_value or None
        if arg is not None:
            value = arg

    if value is None:
        return None
    if isinstance(value, enum.Enum):
        return repr(str(value.value))
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return repr(value)
    return None


async def _backfill_sqlite_columns(conn) -> None:
    """Add columns the models define that an existing SQLite file lacks.

    Version 2.1.1 (2026-11-20): this used to emit a bare
    `ALTER TABLE t ADD COLUMN c TYPE` with no DEFAULT, so every row already in
    the table got NULL — including in columns the models declare non-nullable
    with a default (`auction_mode`, `rtm_enabled`, `team_min_players`,
    `version`, ...). Reading such a row then failed Pydantic validation, which
    surfaced as a 500; and because an unhandled error response carries no CORS
    headers, the browser reported it as a network failure rather than a server
    error. A database upgraded in place was silently unusable.

    Two changes: new columns are added WITH their default, which SQLite applies
    to existing rows, and every non-nullable column with a usable default gets a
    NULL-repair pass. The repair pass matters because it also heals databases
    already damaged by the previous version of this function.
    """
    for table in Base.metadata.tables.values():
        exists = await conn.run_sync(
            lambda sync_conn, t=table: sa.inspect(sync_conn).has_table(t.name)
        )
        if not exists:
            continue

        # SQLAlchemy 2.x returns ReflectedColumn, a TypedDict — so the name is
        # c["name"], not c.name. The 1.x code used getattr(c, "name", None),
        # which yields None for every entry: db_cols came back empty, EVERY
        # column looked missing, every ALTER failed as a duplicate, and the
        # loop skipped on. Genuinely new columns still got added (the ALTER
        # succeeded), which is why this went unnoticed — but nothing that
        # depended on knowing a column already existed ever ran.
        db_cols = await conn.run_sync(
            lambda sync_conn, t=table: {
                c["name"] if isinstance(c, dict) else getattr(c, "name", None)
                for c in sa.inspect(sync_conn).get_columns(t.name)
            }
        )
        db_cols = {c for c in (db_cols or set()) if c is not None}

        for col in table.columns:
            literal = _default_literal(col)

            if col.name not in db_cols:
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {_sqlite_type(col)}'
                if literal is not None:
                    # SQLite applies this to every existing row.
                    ddl += f" DEFAULT {literal}"
                try:
                    await conn.execute(text(ddl))
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug(
                        "SQLite backfill could not add %s.%s: %s", table.name, col.name, exc
                    )
                    continue

            # Repair pass: fill NULLs in a column that must not be null. Covers
            # rows left behind by the pre-2.1.1 version of this function.
            if literal is not None and not col.nullable:
                try:
                    result = await conn.execute(
                        text(
                            f'UPDATE "{table.name}" SET "{col.name}" = {literal} '
                            f'WHERE "{col.name}" IS NULL'
                        )
                    )
                    if result.rowcount:
                        logger.info(
                            "Repaired %s NULL row(s) in %s.%s",
                            result.rowcount, table.name, col.name,
                        )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug(
                        "SQLite NULL repair skipped %s.%s: %s", table.name, col.name, exc
                    )


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
        # create_all skips indexes on pre-existing tables; enforce the one-active
        # player invariant explicitly when upgrading an existing local database.
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_auction_player_one_active_per_event "
            "ON auction_players (event_id) WHERE status = 'active'"
        ))


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
