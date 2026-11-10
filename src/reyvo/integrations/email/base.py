from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProviderAccountInfo:
    email: str
    display_name: str | None = None


@dataclass(slots=True)
class OutboundEmail:
    to_email: str
    subject: str
    body_html: str
    body_text: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SendResult:
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    rfc_message_id: str | None = None


class EmailAdapter(Protocol):
    provider: str

    def build_authorization_url(self, *, state: str) -> str: ...

    async def exchange_code(self, *, code: str) -> OAuthTokens: ...

    async def refresh_access_token(self, *, refresh_token: str) -> OAuthTokens: ...

    async def fetch_account_info(self, *, access_token: str) -> ProviderAccountInfo: ...

    async def send_email(self, *, access_token: str, email: OutboundEmail) -> SendResult: ...


class EmailProviderError(RuntimeError):
    """Raised when an email provider rejects a request."""
