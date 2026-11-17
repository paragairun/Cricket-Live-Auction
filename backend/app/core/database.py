from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def ensure_schema() -> None:
    """Create all tables and add any columns missing from an existing SQLite DB.

    Alembic targets PostgreSQL locally, so we do a lightweight self-migration
    here: create_all for brand-new DBs, plus ALTER TABLE for columns that the
    models define but the running SQLite file doesn't yet have.
    """
    import sqlalchemy as sa

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Only SQLite needs the manual column backfill (PostgreSQL uses Alembic).
        if "sqlite" in str(settings.DATABASE_URL):
            for table in Base.metadata.tables.values():
                # Skip tables that don't exist yet (create_all just made them).
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
                    if col.name not in db_cols:
                        try:
                            await conn.execute(
                                text(
                                    f'ALTER TABLE {table.name} ADD COLUMN {col.name} '
                                    f"{_sqlite_type(col)}"
                                )
                            )
                        except Exception:
                            # Column may already exist (race or create_all covered it); skip.
                            pass


def _sqlite_type(col) -> str:
    """Map a SQLAlchemy column type to a SQLite-compatible DDL fragment."""
    from sqlalchemy import Boolean, Integer, String, Text, DateTime, Enum

    col_type = col.type
    if isinstance(col_type, Boolean):
        return "INTEGER"
    if isinstance(col_type, Integer):
        return "INTEGER"
    if isinstance(col_type, String):
        return "TEXT"
    if isinstance(col_type, Text):
        return "TEXT"
    if isinstance(col_type, DateTime):
        return "TIMESTAMP"
    if isinstance(col_type, Enum):
        return "TEXT"
    return "TEXT"


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
