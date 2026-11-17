import redis.asyncio as aioredis
from app.core.config import settings

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """
    Return a Redis client.

    Tries the configured Redis server first. When no Redis server is available
    (local development without Docker), falls back to an in-memory fakeredis
    instance so WebSocket viewer tracking, magic codes and invite tokens keep
    working without any extra infrastructure.
    """
    global _redis
    if _redis is None:
        try:
            real = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            await real.ping()
            _redis = real
        except Exception:
            import fakeredis.aioredis as _fakeredis  # noqa: WPS433

            _redis = _fakeredis.FakeRedis(decode_responses=True)
    return _redis


async def close_redis():
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
