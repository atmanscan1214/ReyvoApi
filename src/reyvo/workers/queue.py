"""Arq job queue helpers used by the API and the worker process.

Jobs are optional: when ``REDIS_URL`` is blank or ``BACKGROUND_JOBS_ENABLED`` is
false the helpers return ``None`` and callers should fall back to synchronous
work. Enqueue failures are logged and surfaced as ``None`` rather than failing a
request, so a Redis outage degrades the API instead of taking it down.
"""

from __future__ import annotations

import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from redis.exceptions import RedisError

from reyvo.core.config import get_settings

logger = logging.getLogger(__name__)

JOB_QUEUE_NAME = "reyvo"
_queue: ArqRedis | None = None


def job_redis_settings() -> RedisSettings | None:
    """Build Arq connection settings from ``REDIS_URL``."""

    url = get_settings().redis_url
    if not url:
        return None
    try:
        settings = RedisSettings.from_dsn(url)
    except ValueError:
        logger.error("REDIS_URL is not a valid Redis DSN.", exc_info=True)
        return None
    settings.conn_timeout = get_settings().redis_timeout_seconds
    return settings


async def get_job_queue() -> ArqRedis | None:
    """Return the shared Arq pool, creating it on first use."""

    global _queue
    settings = get_settings()
    if not settings.redis_url or not settings.background_jobs_enabled:
        return None
    if _queue is None:
        redis_settings = job_redis_settings()
        if redis_settings is None:
            return None
        try:
            _queue = await create_pool(redis_settings, default_queue_name=JOB_QUEUE_NAME)
        except (RedisError, OSError):
            logger.warning("Could not connect to the job queue.", exc_info=True)
            return None
    return _queue


async def enqueue_job(name: str, *args: object, **kwargs: object) -> str | None:
    """Enqueue a job by function name and return its id, or ``None``."""

    global _queue
    queue = await get_job_queue()
    if queue is None:
        return None
    try:
        job = await queue.enqueue_job(name, *args, **kwargs)
    except (RedisError, OSError):
        logger.warning("Could not enqueue job %s.", name, exc_info=True)
        _queue = None
        return None
    return job.job_id if job is not None else None


async def close_job_queue() -> None:
    global _queue
    if _queue is not None:
        await _queue.aclose()
        _queue = None


def reset_job_queue() -> None:
    """Drop the cached pool without closing it (used by tests)."""

    global _queue
    _queue = None
