"""Redis client.

Version: 2.0.0 (2026-11-20)
Changed from 1.x: the fakeredis fallback used to trigger on ANY exception, in
any environment, silently. In production that meant a momentary Redis outage at
first-call time permanently switched that worker to a private in-memory store —
pub/sub broadcasts then reached only that worker's own sockets, timer ownership
became process-local, and two workers ran two divergent auctions with nothing
logged. The fallback is now opt-in (ALLOW_FAKE_REDIS) and local-only, it logs
loudly when it engages, and a failed real connection is no longer cached, so the
next call retries instead of degrading forever.
"""
import logging

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None
_is_fake: bool = False


async def get_redis() -> aioredis.Redis:
    global _redis, _is_fake

    if _redis is not None:
        return _redis

    try:
        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            health_check_interval=30,
            socket_keepalive=True,
        )
        await client.ping()
        _redis = client
        _is_fake = False
        return _redis
    except Exception as exc:
        if not settings.fake_redis_allowed:
            # Do NOT cache the failure: the next call retries a real connection.
            logger.error("Redis unreachable at %s: %s", settings.REDIS_URL, exc)
            raise RuntimeError(
                f"Redis is unreachable at {settings.REDIS_URL}. The auction engine "
                "requires Redis for timer coordination and cross-worker broadcast. "
                "Set ALLOW_FAKE_REDIS=true with ENVIRONMENT=local for offline development."
            ) from exc

        import fakeredis.aioredis as _fakeredis

        logger.warning(
            "REDIS UNAVAILABLE (%s) — falling back to in-memory fakeredis. "
            "This is local development only: cross-worker broadcast and timer "
            "ownership will NOT work.",
            exc,
        )
        _redis = _fakeredis.FakeRedis(decode_responses=True)
        _is_fake = True
        return _redis


def is_fake_redis() -> bool:
    """True when the in-memory substitute is in use. Surfaced on /api/health."""
    return _is_fake


async def close_redis():
    global _redis, _is_fake
    if _redis:
        await _redis.aclose()
        _redis = None
        _is_fake = False
