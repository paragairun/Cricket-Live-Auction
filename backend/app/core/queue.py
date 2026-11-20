"""ARQ job queue wrapper.

Version: 2.0.0 (2026-11-20)

Changed from 1.x: when Redis was unavailable `get_queue()` set the pool to None
and cached it, then `enqueue()` called `pool.enqueue_job(...)` on None, caught
the resulting AttributeError and logged it. Every invitation, sold notification
and completion summary was dropped with nothing surfaced anywhere. Failures are
no longer cached, so the next call retries, and `enqueue` reports whether the
job was actually queued.

Adds `enqueue_at` for deferred jobs, which is how the auto-hammer survives a
worker restart — previously the countdown was an asyncio.Task that died with
its process and left the player on the block forever.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool = None


async def get_queue():
    """Return an ARQ pool, or None when Redis cannot be reached."""
    global _pool
    if _pool is not None:
        return _pool
    try:
        _pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        return _pool
    except Exception as exc:
        # Not cached: the next call retries rather than degrading permanently.
        logger.error("Job queue unavailable (%s): %s", settings.REDIS_URL, exc)
        return None


async def enqueue(task_name: str, *args: Any, **kwargs: Any) -> bool:
    """Queue a job. Returns True when it was accepted."""
    pool = await get_queue()
    if pool is None:
        logger.error("DROPPED job %s — queue unavailable", task_name)
        return False
    try:
        await pool.enqueue_job(task_name, *args, **kwargs)
        logger.info("Queued %s", task_name)
        return True
    except Exception as exc:
        logger.error("Failed to queue %s: %s", task_name, exc)
        return False


async def enqueue_at(
    task_name: str,
    run_at_epoch: float,
    *args: Any,
    job_id: str | None = None,
    **kwargs: Any,
) -> bool:
    """Queue a job to run at an absolute time.

    `job_id` makes the enqueue idempotent: re-queueing the same deadline for the
    same player is a no-op rather than a second timer.
    """
    pool = await get_queue()
    if pool is None:
        logger.warning(
            "Deferred job %s not queued — queue unavailable. The watchdog sweep "
            "will pick this up instead.",
            task_name,
        )
        return False
    try:
        await pool.enqueue_job(
            task_name,
            *args,
            _defer_until=datetime.fromtimestamp(run_at_epoch, tz=timezone.utc),
            _job_id=job_id,
            **kwargs,
        )
        return True
    except Exception as exc:
        logger.error("Failed to queue deferred %s: %s", task_name, exc)
        return False
