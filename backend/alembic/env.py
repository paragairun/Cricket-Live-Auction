import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

config = context.config
fileConfig(config.config_file_name)

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.models import *  # noqa: F401,F403,E402

target_metadata = Base.metadata


def get_url() -> str:
    """Prefer DATABASE_URL over alembic.ini.

    Version 2.0.0 (2026-11-20): alembic.ini hardcoded
    postgresql+asyncpg://auction:auction@db:5432/auction — credentials in a
    committed file, and a hostname that only resolves inside Docker, so
    `alembic upgrade head` could not be run anywhere else.
    """
    return settings.DATABASE_URL or config.get_main_option("sqlalchemy.url")


def run_migrations_offline():
    url = get_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    url = get_url()
    connectable = create_async_engine(url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
