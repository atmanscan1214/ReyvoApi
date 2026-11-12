from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Reyvo API"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    app_public_url: str = "http://localhost:3000"
    log_level: str = "INFO"

    # Authentication
    secret_key: str = "local-development-secret-change-me-please"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    jwt_algorithm: str = "HS256"

    # OAuth token encryption
    token_encryption_key: str | None = None

    # Persistence
    database_url: str = "postgresql+psycopg://reyvo:reyvo@localhost:5432/reyvo"
    auto_create_tables: bool = False
    # Optional shared Redis. When blank, rate limiting stays in-process and the
    # job queue is disabled. Docker Compose sets this to the bundled service.
    redis_url: str | None = None
    redis_timeout_seconds: int = 2

    # Background jobs
    background_jobs_enabled: bool = True
    # Delete chat conversations whose last activity is older than this. 0 keeps
    # them indefinitely. Run the cleanup job from reyvo.workers.worker.
    chat_retention_days: int = 90

    # HTTP
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # Email providers
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/api/v1/email-accounts/oauth/google/callback"

    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None
    microsoft_tenant_id: str = "common"
    microsoft_redirect_uri: str = (
        "http://localhost:8000/api/v1/email-accounts/oauth/microsoft/callback"
    )

    # AI
    ai_api_key: str | None = None
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4.1-mini"
    ai_embedding_model: str = "text-embedding-3-small"

    # Knowledge base
    design_doc_path: str = "Reyvo_Detailed_Design_Document_v1.0.docx"
    knowledge_top_k: int = 6
    # "platform_admin" keeps the internal design corpus private; set to "members"
    # to let every authenticated tenant member use the assistant.
    knowledge_chat_access: Literal["platform_admin", "members"] = "platform_admin"

    # AI chat guardrails (shared through Redis when configured)
    ai_chat_rate_limit_per_minute: int = 20

    # WorkOS SSO (enterprise sign-in alongside local password auth)
    workos_api_key: str | None = None
    workos_client_id: str | None = None
    workos_redirect_uri: str = "http://localhost:8000/api/v1/auth/sso/callback"
    workos_webhook_secret: str | None = None

    # Billing
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_starter: str | None = None
    stripe_price_growth: str | None = None
    stripe_price_scale: str | None = None

    # Sending safety
    default_daily_cap: int = 50
    default_hourly_cap: int = 25
    require_email_account_for_launch: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def workos_sso_configured(self) -> bool:
        return bool(self.workos_api_key and self.workos_client_id)

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be a standard logging level")
        return level


@lru_cache(maxsize=1)
def _settings_from_env() -> Settings:
    return Settings()


_settings_override: Settings | None = None


def get_settings() -> Settings:
    return _settings_override or _settings_from_env()


def set_settings(settings: Settings | None) -> None:
    """Override settings for an application instance (used by tests)."""

    global _settings_override
    _settings_override = settings


def reset_settings() -> None:
    set_settings(None)
    _settings_from_env.cache_clear()
