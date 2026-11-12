"""Sliding-window rate limiting with a Redis backend and in-process fallback.

The Redis implementation uses a sorted set per key and a Lua script so the
read-modify-write is atomic across workers. When Redis is not configured (or is
temporarily unreachable) the service falls back to an in-process limiter, which
keeps a single-worker deployment protected and degrades gracefully during a
Redis outage. The fallback is per process, so cross-worker limits become looser
while Redis is down; the next successful call restores the shared limit.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from reyvo.core.redis import get_redis

logger = logging.getLogger(__name__)

_KEY_PREFIX = "reyvo:ratelimit:"
_MAX_KEYS = 10_000
_REDIS_RETRY_SECONDS = 30.0

_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = window
  if oldest[2] then
    retry_ms = tonumber(oldest[2]) + window - now
  end
  redis.call('PEXPIRE', key, window)
  local retry = math.ceil(retry_ms / 1000)
  if retry < 1 then retry = 1 end
  return {0, count, retry}
end

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
local remaining = limit - (count + 1)
if remaining < 0 then remaining = 0 end
return {1, remaining, 0}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int
    backend: str


class InMemoryRateLimiter:
    """Process-local sliding window keyed by caller identity."""

    backend = "memory"

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        if limit <= 0:
            return RateLimitResult(True, limit, 0, 0, self.backend)

        now = self._clock()
        cutoff = now - window_seconds
        async with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()

            if len(events) >= limit:
                retry_after = max(1, math.ceil(events[0] + window_seconds - now))
                return RateLimitResult(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    retry_after_seconds=retry_after,
                    backend=self.backend,
                )

            events.append(now)
            remaining = max(0, limit - len(events))
            if len(self._events) > _MAX_KEYS:
                self._prune(cutoff)

        return RateLimitResult(True, limit, remaining, 0, self.backend)

    def _prune(self, cutoff: float) -> None:
        stale = [key for key, events in self._events.items() if not events or events[-1] <= cutoff]
        for key in stale:
            del self._events[key]

    def reset(self) -> None:
        self._events.clear()


class RedisRateLimiter:
    """Shared sliding window evaluated atomically inside Redis."""

    backend = "redis"

    def __init__(self, client: Redis) -> None:
        self._script = client.register_script(_SLIDING_WINDOW_SCRIPT)

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        if limit <= 0:
            return RateLimitResult(True, limit, 0, 0, self.backend)

        now_ms = int(time.time() * 1000)
        member = f"{now_ms}-{uuid.uuid4().hex}"
        allowed, remaining, retry_after = await self._script(
            keys=[f"{_KEY_PREFIX}{key}"],
            args=[now_ms, window_seconds * 1000, limit, member],
        )
        return RateLimitResult(
            allowed=bool(int(allowed)),
            limit=limit,
            remaining=max(0, int(remaining)),
            retry_after_seconds=max(0, int(retry_after)),
            backend=self.backend,
        )


class RateLimitService:
    """Choose Redis when available, otherwise use the in-process window."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._memory = InMemoryRateLimiter(clock=clock)
        self._redis_cooldown_until = 0.0

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        if limit <= 0:
            return RateLimitResult(True, limit, 0, 0, "disabled")

        client = get_redis()
        if client is not None and self._clock() >= self._redis_cooldown_until:
            try:
                return await RedisRateLimiter(client).hit(
                    key, limit=limit, window_seconds=window_seconds
                )
            except (RedisError, OSError):
                logger.warning(
                    "Redis rate limiter unavailable; using the in-process fallback for %ss.",
                    int(_REDIS_RETRY_SECONDS),
                    exc_info=True,
                )
                self._redis_cooldown_until = self._clock() + _REDIS_RETRY_SECONDS

        return await self._memory.hit(key, limit=limit, window_seconds=window_seconds)

    def reset(self) -> None:
        self._memory.reset()
        self._redis_cooldown_until = 0.0


rate_limiter = RateLimitService()


def reset_rate_limiter() -> None:
    """Clear in-process state and the Redis cooldown (used by tests)."""

    rate_limiter.reset()
