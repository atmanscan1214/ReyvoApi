from __future__ import annotations

from reyvo.core.config import get_settings, set_settings
from reyvo.core.redis import ping_redis
from reyvo.workers.queue import enqueue_job, get_job_queue, job_redis_settings


async def test_redis_is_disabled_without_a_url():
    assert await ping_redis() is False


async def test_job_queue_is_disabled_without_redis():
    assert await get_job_queue() is None
    assert await enqueue_job("cleanup_chat_history") is None


def test_job_redis_settings_parses_dsn():
    base = get_settings()
    set_settings(base.model_copy(update={"redis_url": "redis://:secret@example.test:6380/2"}))

    settings = job_redis_settings()

    assert settings is not None
    assert settings.host == "example.test"
    assert settings.port == 6380
    assert settings.database == 2
    assert settings.password == "secret"
    assert settings.conn_timeout == base.redis_timeout_seconds
