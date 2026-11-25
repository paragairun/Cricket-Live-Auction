"""Database-backed auction timer running inside the FastAPI process."""
import asyncio
import logging

from app.core.database import AsyncSessionLocal
from app.services.auction_service import run_due_hammers

logger = logging.getLogger(__name__)


class LocalAuctionTimer:
    def __init__(self, interval_seconds: float = 0.5):
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None

    async def startup(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="local-auction-timer")
            logger.info("Local database-backed auction timer started")

    async def shutdown(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self):
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    await run_due_hammers(db)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Local auction timer cycle failed")
            await asyncio.sleep(self.interval_seconds)


timer = LocalAuctionTimer()
