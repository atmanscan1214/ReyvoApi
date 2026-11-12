from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from reyvo.core.config import Settings, reset_settings
from reyvo.core.rate_limit import reset_rate_limiter
from reyvo.core.redis import reset_redis
from reyvo.db.enums import EmailAccountStatus, EmailProvider
from reyvo.db.models import EmailAccount
from reyvo.integrations.ai.factory import reset_ai_providers
from reyvo.main import create_app
from reyvo.workers.queue import reset_job_queue

TEST_SECRET = "reyvo-test-secret-key-that-is-long-enough"


@pytest.fixture
async def app():
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        auto_create_tables=True,
        secret_key=TEST_SECRET,
        require_email_account_for_launch=False,
        log_level="WARNING",
        workos_api_key="sk_test_workos_api_key",
        workos_client_id="client_test_workos",
        workos_redirect_uri="http://test/api/v1/auth/sso/callback",
        # Integration tests register regular members; keep the assistant usable
        # for them while the admin-only deployment default stays unchanged.
        knowledge_chat_access="members",
        redis_url=None,
        background_jobs_enabled=False,
    )
    application = create_app(settings)
    async with LifespanManager(application):
        yield application
    reset_settings()
    reset_ai_providers()
    reset_rate_limiter()
    reset_redis()
    reset_job_queue()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
def register_account(client: AsyncClient) -> Callable[..., object]:
    async def _register(
        *,
        email: str = "owner@example.com",
        password: str = "correct-horse-battery-staple",
        full_name: str = "Avery Owner",
        agency_name: str = "Acme Staffing",
    ):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "full_name": full_name,
                "agency_name": agency_name,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _register


@pytest.fixture
def auth_headers() -> Callable[[dict], dict[str, str]]:
    def _headers(token_pair: dict) -> dict[str, str]:
        return {"Authorization": f"Bearer {token_pair['access_token']}"}

    return _headers


@pytest.fixture
def seed_email_account(app) -> Callable[..., object]:
    async def _seed(tenant_id: str, email: str = "sender@acme.com") -> uuid.UUID:
        session_factory = app.state.session_factory
        account_id = uuid.uuid4()
        async with session_factory() as session:
            session.add(
                EmailAccount(
                    id=account_id,
                    tenant_id=uuid.UUID(tenant_id),
                    provider=EmailProvider.GOOGLE,
                    email=email,
                    status=EmailAccountStatus.CONNECTED,
                    daily_cap=50,
                    hourly_cap=25,
                )
            )
            await session.commit()
        return account_id

    return _seed
