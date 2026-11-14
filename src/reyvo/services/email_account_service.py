from __future__ import annotations

import html
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.config import get_settings
from reyvo.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from reyvo.core.security import create_oauth_state, decode_token, encrypt_secret
from reyvo.core.tenancy import Principal
from reyvo.core.utils import normalize_email, utcnow
from reyvo.db.enums import EmailAccountStatus, EmailProvider
from reyvo.db.models import EmailAccount
from reyvo.integrations.email.base import EmailProviderError, OutboundEmail
from reyvo.integrations.email.oauth import get_email_adapter
from reyvo.schemas.email_account import (
    EmailAccountTestRequest,
    EmailAccountUpdate,
    OAuthStartResponse,
    SmtpAccountCreate,
)
from reyvo.services import audit_service, email_delivery


async def list_email_accounts(session: AsyncSession, tenant_id: uuid.UUID) -> list[EmailAccount]:
    stmt = (
        select(EmailAccount)
        .where(EmailAccount.tenant_id == tenant_id)
        .order_by(EmailAccount.created_at)
    )
    return list((await session.scalars(stmt)).all())


async def get_email_account_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, account_id: uuid.UUID
) -> EmailAccount:
    account = await session.scalar(
        select(EmailAccount).where(
            EmailAccount.id == account_id, EmailAccount.tenant_id == tenant_id
        )
    )
    if account is None:
        raise NotFoundError("Sending inbox not found.")
    return account


async def update_email_account(
    session: AsyncSession,
    principal: Principal,
    account_id: uuid.UUID,
    payload: EmailAccountUpdate,
) -> EmailAccount:
    account = await get_email_account_or_404(session, principal.tenant_id, account_id)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(account, field, value)
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="email_account.updated",
        actor_id=principal.user_id,
        entity_type="email_account",
        entity_id=account.id,
        after_data={key: str(value) for key, value in data.items()},
    )
    await session.commit()
    await session.refresh(account)
    return account


async def disconnect_email_account(
    session: AsyncSession, principal: Principal, account_id: uuid.UUID
) -> EmailAccount:
    account = await get_email_account_or_404(session, principal.tenant_id, account_id)
    account.status = EmailAccountStatus.DISCONNECTED
    account.access_token_enc = None
    account.refresh_token_enc = None
    account.token_expires_at = None
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="email_account.disconnected",
        actor_id=principal.user_id,
        entity_type="email_account",
        entity_id=account.id,
        before_data={"email": account.email},
    )
    await session.commit()
    await session.refresh(account)
    return account


async def start_oauth(
    session: AsyncSession,
    principal: Principal,
    provider: EmailProvider,
) -> OAuthStartResponse:
    existing = await session.scalar(
        select(EmailAccount.id).where(
            EmailAccount.tenant_id == principal.tenant_id,
            EmailAccount.provider == provider,
            EmailAccount.status == EmailAccountStatus.CONNECTED,
        )
    )
    if existing is not None:
        # Multiple inboxes per provider are allowed; this is informational only.
        pass

    adapter = get_email_adapter(provider)
    state = create_oauth_state(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        provider=provider.value,
    )
    return OAuthStartResponse(
        authorization_url=adapter.build_authorization_url(state=state),
        state=state,
    )


async def complete_oauth(
    session: AsyncSession,
    *,
    provider: EmailProvider,
    code: str,
    state: str,
) -> EmailAccount:
    claims = decode_token(state, expected_type="oauth_state")
    if claims.get("provider") != provider.value:
        raise UnauthorizedError("The OAuth state does not match this provider.")
    try:
        tenant_id = uuid.UUID(str(claims["tid"]))
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("The OAuth state is malformed.") from exc

    adapter = get_email_adapter(provider)
    tokens = await adapter.exchange_code(code=code)
    info = await adapter.fetch_account_info(access_token=tokens.access_token)
    email = normalize_email(info.email)

    account = await session.scalar(
        select(EmailAccount).where(
            EmailAccount.tenant_id == tenant_id,
            EmailAccount.email == email,
        )
    )
    if account is None:
        account = EmailAccount(
            tenant_id=tenant_id,
            provider=provider,
            email=email,
            created_by=user_id,
        )
        session.add(account)
    elif account.provider != provider:
        raise ConflictError("This email address is already connected through a different provider.")

    account.display_name = info.display_name
    account.from_name = account.from_name or info.display_name
    account.status = EmailAccountStatus.CONNECTED
    account.scopes = tokens.scopes
    account.access_token_enc = encrypt_secret(tokens.access_token)
    if tokens.refresh_token:
        account.refresh_token_enc = encrypt_secret(tokens.refresh_token)
    account.token_expires_at = tokens.expires_at
    account.last_error = None

    await session.flush()
    await audit_service.record(
        session,
        tenant_id=tenant_id,
        action="email_account.connected",
        actor_id=user_id,
        entity_type="email_account",
        entity_id=account.id,
        after_data={"email": account.email, "provider": provider.value},
    )
    await session.commit()
    await session.refresh(account)
    return account


async def register_smtp_account(
    session: AsyncSession,
    principal: Principal,
    payload: SmtpAccountCreate,
) -> EmailAccount:
    """Register the deployment-level SMTP sender as a connected inbox."""

    settings = get_settings()
    if not settings.smtp_configured:
        raise ServiceUnavailableError(
            "SMTP is not configured. Set SMTP_HOST and either SMTP_FROM_EMAIL "
            "or SMTP_USERNAME."
        )

    email = normalize_email(str(settings.smtp_from_email or settings.smtp_username))
    account = await session.scalar(
        select(EmailAccount).where(
            EmailAccount.tenant_id == principal.tenant_id,
            EmailAccount.email == email,
        )
    )
    if account is not None and account.provider != EmailProvider.SMTP:
        raise ConflictError("This email address is already connected through a different provider.")
    if account is None:
        account = EmailAccount(
            tenant_id=principal.tenant_id,
            provider=EmailProvider.SMTP,
            email=email,
            created_by=principal.user_id,
        )
        session.add(account)

    account.provider = EmailProvider.SMTP
    account.status = EmailAccountStatus.CONNECTED
    account.display_name = settings.smtp_from_name or account.display_name
    account.from_name = payload.from_name or settings.smtp_from_name or account.from_name
    account.daily_cap = payload.daily_cap or settings.default_daily_cap
    account.hourly_cap = payload.hourly_cap or settings.default_hourly_cap
    account.last_error = None

    await session.flush()
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="email_account.connected",
        actor_id=principal.user_id,
        entity_type="email_account",
        entity_id=account.id,
        after_data={"email": account.email, "provider": EmailProvider.SMTP.value},
    )
    await session.commit()
    await session.refresh(account)
    return account


async def send_test_email(
    session: AsyncSession,
    principal: Principal,
    account_id: uuid.UUID,
    payload: EmailAccountTestRequest,
) -> dict[str, object]:
    """Send one real message through the inbox to verify delivery."""

    account = await get_email_account_or_404(session, principal.tenant_id, account_id)
    settings = get_settings()
    to_email = payload.to_email or settings.smtp_test_recipient
    if not to_email:
        raise BadRequestError(
            "Provide a recipient in the request body or set SMTP_TEST_RECIPIENT."
        )

    body_text = payload.message or (
        "This is a test message from Reyvo. If you received it, the sending "
        "configuration is working."
    )
    outbound = OutboundEmail(
        to_email=str(to_email),
        subject=payload.subject,
        body_html=f"<p>{html.escape(body_text)}</p>",
        body_text=body_text,
        from_name=account.from_name or account.display_name,
        reply_to=account.reply_to,
    )

    try:
        result = await email_delivery.deliver_email(session, account, outbound)
    except EmailProviderError as exc:
        account.status = EmailAccountStatus.DEGRADED
        account.last_error = str(exc)[:1_000]
        await session.commit()
        raise ServiceUnavailableError(f"Test email failed: {exc}") from exc

    account.last_health_check_at = utcnow()
    account.last_error = None
    await session.commit()
    return {
        "ok": True,
        "message": f"Test email sent to {to_email}.",
        "provider_message_id": result.provider_message_id,
    }
