"""Shared delivery helpers for OAuth and SMTP sending inboxes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.security import decrypt_secret, encrypt_secret
from reyvo.core.utils import utcnow
from reyvo.db.enums import EmailProvider
from reyvo.db.models import EmailAccount
from reyvo.integrations.email.base import EmailProviderError, OutboundEmail, SendResult
from reyvo.integrations.email.oauth import get_email_adapter

_TOKEN_REFRESH_SKEW = timedelta(seconds=60)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def access_token_for(session: AsyncSession, account: EmailAccount) -> str:
    """Return a valid OAuth access token, refreshing it when needed.

    SMTP accounts do not use OAuth, so they return an empty token.
    """

    if account.provider == EmailProvider.SMTP:
        return ""

    adapter = get_email_adapter(account.provider)
    expires_at = _as_aware(account.token_expires_at)
    if account.access_token_enc and (
        expires_at is None or expires_at > utcnow() + _TOKEN_REFRESH_SKEW
    ):
        return decrypt_secret(account.access_token_enc)

    if not account.refresh_token_enc:
        raise EmailProviderError(
            "The sending inbox has no usable token. Reconnect it before sending."
        )

    tokens = await adapter.refresh_access_token(
        refresh_token=decrypt_secret(account.refresh_token_enc)
    )
    account.access_token_enc = encrypt_secret(tokens.access_token)
    if tokens.refresh_token:
        account.refresh_token_enc = encrypt_secret(tokens.refresh_token)
    account.token_expires_at = tokens.expires_at
    await session.flush()
    return tokens.access_token


async def deliver_email(
    session: AsyncSession,
    account: EmailAccount,
    email: OutboundEmail,
) -> SendResult:
    adapter = get_email_adapter(account.provider)
    token = await access_token_for(session, account)
    return await adapter.send_email(access_token=token, email=email)
