"""Small in-process dispatcher for non-critical email notifications.

Auction state and deadlines are persisted in the database. Email is a side
effect: failures are logged and never hold up an auction command.
"""
import asyncio
import logging

from app.services import email_service

logger = logging.getLogger(__name__)
_TASKS: set[asyncio.Task] = set()
_EMAIL_FUNCTIONS = {
    "task_send_event_invitation": email_service.send_event_invitation,
    "task_send_organizer_invite": email_service.send_organizer_invite,
    "task_send_magic_code": email_service.send_magic_code,
    "task_send_welcome": email_service.send_welcome,
    "task_send_auction_starting": email_service.send_auction_starting,
    "task_send_event_completion_summary": email_service.send_event_completion_summary,
}


def _finished(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Background email task failed")


async def dispatch_email(task_name: str, *args) -> bool:
    function = _EMAIL_FUNCTIONS.get(task_name)
    if function is None:
        logger.error("Unknown background email task: %s", task_name)
        return False
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    _TASKS.add(task)
    task.add_done_callback(_finished)
    return True


async def shutdown_background_tasks() -> None:
    if not _TASKS:
        return
    pending = list(_TASKS)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    _TASKS.clear()
