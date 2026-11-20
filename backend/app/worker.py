"""ARQ background worker.

Version: 2.0.0 (2026-11-20)

New in 2.0.0: `task_auto_hammer` and the `watchdog_sweep` cron job. The player
countdown used to be an asyncio.Task living inside an API worker process,
coordinated by a Redis key holding a PID. If that process restarted the task
died silently, the player stayed `active` forever and the hammer never fell —
the auction simply stopped until someone intervened. PIDs also collide across
containers, so one worker could read another's PID and conclude it owned a timer
it did not own.

The deadline now lives in Redis and the hammer fires from a deferred job here.
The cron sweep is the second line of defence: it hammers anything whose deadline
has passed, which covers a dropped job, a worker that was down at the deadline,
and local development with no ARQ worker running at all.
"""
import logging

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services import auction_service, email_service

logger = logging.getLogger(__name__)


# ─── Auction tasks ────────────────────────────────────────────────────────────

async def task_auto_hammer(ctx: dict, event_id: int, auction_player_id: int):
    """Fire the hammer when a player's countdown expires.

    Re-reads the deadline before acting, so a bid that extended the clock makes
    this job a no-op and the newer job takes over. That is why no job
    cancellation is needed.
    """
    async with AsyncSessionLocal() as db:
        try:
            await auction_service.auto_hammer(event_id, auction_player_id, db)
        except Exception as exc:
            logger.exception(
                "auto_hammer failed (event=%s, auction_player=%s): %s",
                event_id, auction_player_id, exc,
            )


async def watchdog_sweep(ctx: dict):
    """Hammer any event whose deadline has passed. Runs every 5 seconds."""
    try:
        count = await auction_service.run_due_hammers(AsyncSessionLocal)
        if count:
            logger.warning("Watchdog hammered %s overdue player(s)", count)
    except Exception as exc:
        logger.exception("Watchdog sweep failed: %s", exc)


# ─── Email tasks ──────────────────────────────────────────────────────────────

async def task_send_event_invitation(
    ctx: dict, to: str, name: str, event_name: str, event_id: int, role: str
):
    email_service.send_event_invitation(to, name, event_name, event_id, role)


async def task_send_organizer_invite(ctx: dict, to: str, event_name: str, token: str, role: str):
    email_service.send_organizer_invite(to, event_name, token, role)


async def task_send_magic_code(ctx: dict, to: str, name: str, code: str):
    email_service.send_magic_code(to, name, code)


async def task_send_welcome(ctx: dict, to: str, name: str):
    email_service.send_welcome(to, name)


async def task_send_auction_starting(
    ctx: dict, to: str, name: str, event_name: str, event_id: int, role: str
):
    email_service.send_auction_starting(to, name, event_name, event_id, role)


async def task_send_event_completion_summary(
    ctx: dict, to: str, name: str, event_name: str, event_id: int, summary: dict
):
    email_service.send_event_completion_summary(to, name, event_name, event_id, summary)


# ─── Worker configuration ─────────────────────────────────────────────────────

class WorkerSettings:
    functions = [
        task_auto_hammer,
        task_send_event_invitation,
        task_send_organizer_invite,
        task_send_magic_code,
        task_send_welcome,
        task_send_auction_starting,
        task_send_event_completion_summary,
    ]
    cron_jobs = [
        cron(watchdog_sweep, second={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 20
    job_timeout = 30
    keep_result = 300
    # A deferred hammer must not be dropped because the worker was briefly busy.
    max_tries = 3
