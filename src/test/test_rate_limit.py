from __future__ import annotations

import fakeredis.aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from reyvo.core.rate_limit import (
    InMemoryRateLimiter,
    RateLimitService,
    RedisRateLimiter,
)


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_in_memory_limiter_enforces_the_window():
    clock = FakeClock()
    limiter = InMemoryRateLimiter(clock=clock)

    first = await limiter.hit("user", limit=2, window_seconds=60)
    second = await limiter.hit("user", limit=2, window_seconds=60)
    blocked = await limiter.hit("user", limit=2, window_seconds=60)

    assert first.allowed is True and first.remaining == 1
    assert second.allowed is True and second.remaining == 0
    assert blocked.allowed is False
    assert 1 <= blocked.retry_after_seconds <= 60

    clock.advance(60)
    assert (await limiter.hit("user", limit=2, window_seconds=60)).allowed is True


async def test_in_memory_limiter_keys_are_independent():
    limiter = InMemoryRateLimiter()

    assert (await limiter.hit("a", limit=1, window_seconds=60)).allowed is True
    assert (await limiter.hit("a", limit=1, window_seconds=60)).allowed is False
    assert (await limiter.hit("b", limit=1, window_seconds=60)).allowed is True


async def test_redis_limiter_is_shared_between_instances():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        first_worker = RedisRateLimiter(client)
        second_worker = RedisRateLimiter(client)

        assert (await first_worker.hit("shared", limit=2, window_seconds=60)).allowed is True
        assert (await second_worker.hit("shared", limit=2, window_seconds=60)).allowed is True
        blocked = await first_worker.hit("shared", limit=2, window_seconds=60)
        assert blocked.allowed is False
        assert blocked.retry_after_seconds >= 1
    finally:
        await client.aclose()


async def test_service_uses_redis_when_available(monkeypatch):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("reyvo.core.rate_limit.get_redis", lambda: client)
    try:
        service = RateLimitService()
        assert (await service.hit("key", limit=1, window_seconds=60)).backend == "redis"
        assert (await service.hit("key", limit=1, window_seconds=60)).allowed is False
    finally:
        await client.aclose()


class BrokenRedisClient:
    def register_script(self, script: str):
        async def _call(**kwargs: object) -> list[int]:
            raise RedisConnectionError("redis is down")

        return _call


async def test_service_falls_back_when_redis_fails(monkeypatch):
    monkeypatch.setattr("reyvo.core.rate_limit.get_redis", lambda: BrokenRedisClient())
    service = RateLimitService()

    result = await service.hit("key", limit=1, window_seconds=60)

    assert result.allowed is True
    assert result.backend == "memory"


async def test_limit_zero_disables_limiting():
    limiter = InMemoryRateLimiter()

    result = await limiter.hit("key", limit=0, window_seconds=60)

    assert result.allowed is True
