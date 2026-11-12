from __future__ import annotations

import pytest
from reyvo.core.config import Settings, reset_settings, set_settings
from reyvo.core.rate_limit import reset_rate_limiter
from reyvo.core.redis import reset_redis
from reyvo.integrations.ai.factory import reset_ai_providers
from reyvo.workers.queue import reset_job_queue

UNIT_SECRET = "reyvo-unit-test-secret-key-that-is-long-enough"
UNIT_MODEL = "unit-test-model"


@pytest.fixture(autouse=True)
def isolated_settings():
    """Deterministic settings for pure unit tests (no app or database needed)."""

    set_settings(
        Settings(
            environment="test",
            secret_key=UNIT_SECRET,
            database_url="sqlite+aiosqlite:///:memory:",
            log_level="WARNING",
            ai_api_key=None,
            ai_model=UNIT_MODEL,
            ai_base_url="https://example.test/v1",
            redis_url=None,
            background_jobs_enabled=False,
        )
    )
    yield
    reset_settings()
    reset_ai_providers()
    reset_rate_limiter()
    reset_redis()
    reset_job_queue()
