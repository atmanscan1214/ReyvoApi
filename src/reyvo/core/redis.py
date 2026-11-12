"""Shared async Redis client.

Redis is optional. When ``REDIS_URL`` is blank the application uses an
in-process rate limiter and the background job queue stays disabled. Callers
should treat :func:`get_redis` as best-effort and handle ``redis.RedisError``;
the rate limiter does this automatically.
"""

from __future__ import annotations

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from reyvo.core.config import get_settings

logger = logging.getLogger(__name__)

_redis: Redis | None = None


def get_redis() -> Redis | None:
    """Return the process-wide Redis client, or ``None`` when not configured."""

    global _redis
    settings = get_settings()
    if not settings.redis_url:
        return None
    if _redis is None:
        _redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_timeout_seconds,
            socket_timeout=settings.redis_timeout_seconds,
            health_check_interval=30,
        )
    return _redis


async def ping_redis() -> bool:
    """Return ``True`` when Redis is configured and reachable."""

    client = get_redis()
    if client is None:
        return False
    try:
        return bool(await client.ping())
    except (RedisError, OSError):
        logger.warning("Redis health check failed.", exc_info=True)
        return False


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def reset_redis() -> None:
    """Drop the cached client without closing it (used by tests)."""

    global _redis
    _redis = None
