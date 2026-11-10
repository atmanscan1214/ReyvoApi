from __future__ import annotations

import secrets
import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.config import get_settings
from reyvo.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from reyvo.core.security import create_sso_state, decode_sso_state, hash_password
from reyvo.core.tenancy import Principal, set_tenant_context
from reyvo.core.utils import is_valid_email, normalize_email, utcnow
from reyvo.db.enums import MemberRole, MembershipStatus, TenantStatus, UserStatus
from reyvo.db.models import Membership, Tenant, User
from reyvo.integrations.identity.workos_sso import get_workos_client
from reyvo.schemas.sso import SSOAuthorizeResponse, SSOSettingsUpdate, SSOStatus
from reyvo.services import audit_service


def _ensure_tenant_usable(tenant: Tenant) -> None:
    if tenant.status in {TenantStatus.SUSPENDED, TenantStatus.DELETED, TenantStatus.CANCELED}:
        raise ForbiddenError("This workspace is not available. Contact your administrator.")


def status_for_tenant(tenant: Tenant) -> SSOStatus:
    settings = get_settings()
    available = settings.workos_sso_configured
    configured = bool(tenant.workos_organization_id or tenant.workos_sso_connection_id)
    return SSOStatus(
        available=available,
        configured=configured,
        enabled=available and configured,
        organization_id=tenant.workos_organization_id,
        connection_id=tenant.workos_sso_connection_id,
        redirect_uri=settings.workos_redirect_uri if available else None,
    )


async def update_settings(
    session: AsyncSession,
    principal: Principal,
    payload: SSOSettingsUpdate,
) -> SSOStatus:
    tenant = principal.tenant
    data = payload.model_dump(exclude_unset=True)

    normalized: dict[str, str | None] = {}
    for field in ("workos_organization_id", "workos_sso_connection_id"):
        if field not in data:
            continue
        value = data[field]
        normalized[field] = value.strip() or None if isinstance(value, str) else None

    organization_id = normalized.get("workos_organization_id", tenant.workos_organization_id)
    connection_id = normalized.get("workos_sso_connection_id", tenant.workos_sso_connection_id)

    if not organization_id and not connection_id:
        raise BadRequestError(
            "Provide a WorkOS organization ID or SSO connection ID to enable SSO. "
            "Use DELETE to disable SSO."
        )

    if organization_id:
        conflict = await session.scalar(
            select(Tenant.id).where(
                Tenant.workos_organization_id == organization_id,
                Tenant.id != tenant.id,
            )
        )
        if conflict is not None:
            raise ConflictError("Another workspace is already linked to this WorkOS organization.")

    before = {
        "workos_organization_id": tenant.workos_organization_id,
        "workos_sso_connection_id": tenant.workos_sso_connection_id,
    }
    for field, value in normalized.items():
        setattr(tenant, field, value)

    await audit_service.record(
        session,
        tenant_id=tenant.id,
        action="tenant.sso_updated",
        actor_id=principal.user_id,
        entity_type="tenant",
        entity_id=tenant.id,
        before_data=before,
        after_data={
            "workos_organization_id": tenant.workos_organization_id,
            "workos_sso_connection_id": tenant.workos_sso_connection_id,
        },
    )
    await session.commit()
    await session.refresh(tenant)
    return status_for_tenant(tenant)


async def disable_sso(
    session: AsyncSession, principal: Principal, *, reason: str | None = None
) -> SSOStatus:
    tenant = principal.tenant
    before = {
        "workos_organization_id": tenant.workos_organization_id,
        "workos_sso_connection_id": tenant.workos_sso_connection_id,
    }
    tenant.workos_organization_id = None
    tenant.workos_sso_connection_id = None
    await audit_service.record(
        session,
        tenant_id=tenant.id,
        action="tenant.sso_disabled",
        actor_id=principal.user_id,
        entity_type="tenant",
        entity_id=tenant.id,
        before_data=before,
        after_data={"reason": reason},
    )
    await session.commit()
    await session.refresh(tenant)
    return status_for_tenant(tenant)


def _allowed_return_origins() -> set[str]:
    settings = get_settings()
    origins = set(settings.cors_origin_list)
    public = urlparse(settings.app_public_url)
    if public.scheme and public.netloc:
        origins.add(f"{public.scheme}://{public.netloc}")
    return origins


def _validated_return_to(return_to: str | None) -> str | None:
    if not return_to:
        return None
    parsed = urlparse(return_to)
    if not parsed.scheme or not parsed.netloc:
        raise BadRequestError("return_to must be an absolute URL.")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed = _allowed_return_origins()
    if origin not in allowed:
        raise BadRequestError(
            "return_to is not an allowed redirect target.",
            details={"allowed_origins": sorted(allowed)},
        )
    return return_to


async def authorize(
    session: AsyncSession,
    *,
    tenant_slug: str,
    login_hint: str | None = None,
    domain_hint: str | None = None,
    return_to: str | None = None,
) -> SSOAuthorizeResponse:
    return_to = _validated_return_to(return_to)
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == tenant_slug.strip().lower()))
    if tenant is None:
        raise NotFoundError("Workspace not found.")
    _ensure_tenant_usable(tenant)

    if not tenant.workos_organization_id and not tenant.workos_sso_connection_id:
        raise BadRequestError("This workspace has not enabled SSO.")
    if not get_settings().workos_sso_configured:
        raise BadRequestError("WorkOS SSO is not configured on this platform.")

    state = create_sso_state(
        tenant_id=tenant.id,
        return_to=return_to,
        login_hint=login_hint,
    )
    client = get_workos_client()
    url = await client.authorization_url(
        state=state,
        organization_id=tenant.workos_organization_id,
        connection_id=tenant.workos_sso_connection_id,
        login_hint=login_hint,
        domain_hint=domain_hint,
    )
    return SSOAuthorizeResponse(authorization_url=url, state=state)


def _verify_profile(tenant: Tenant, profile: Any) -> None:
    if tenant.workos_organization_id and profile.organization_id != tenant.workos_organization_id:
        raise UnauthorizedError("This SSO identity does not belong to this workspace.")
    if tenant.workos_sso_connection_id and profile.connection_id != tenant.workos_sso_connection_id:
        raise UnauthorizedError(
            "This SSO identity did not sign in through the configured connection."
        )


def _display_name(first_name: str | None, last_name: str | None, email: str) -> str:
    joined = " ".join(part for part in (first_name, last_name) if part).strip()
    return joined or email.split("@", 1)[0]


def _mapped_role(profile: Any) -> MemberRole:
    role = getattr(profile, "role", None)
    slug = getattr(role, "slug", None) if role is not None else None
    if slug == MemberRole.OWNER.value:
        return MemberRole.OWNER
    if slug == MemberRole.ADMIN.value:
        return MemberRole.ADMIN
    return MemberRole.MEMBER


async def complete_login(
    session: AsyncSession,
    *,
    code: str,
    state: str,
) -> Principal:
    claims = decode_sso_state(state)
    try:
        tenant_id = uuid.UUID(str(claims["tid"]))
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("The SSO state is malformed.") from exc

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("Workspace not found.")
    _ensure_tenant_usable(tenant)
    # Membership and audit rows are protected by RLS in production.
    await set_tenant_context(session, tenant.id)

    result = await get_workos_client().profile_and_token(code=code)
    profile = result.profile
    _verify_profile(tenant, profile)

    email = normalize_email(profile.email)
    if not is_valid_email(email):
        raise UnauthorizedError("WorkOS did not return a valid email address.")

    user = await session.scalar(select(User).where(User.workos_user_id == profile.id))
    if user is None:
        user = await session.scalar(select(User).where(User.email == email))
        if user is not None and user.workos_user_id not in (None, profile.id):
            raise UnauthorizedError("This email is already linked to a different SSO identity.")

    first_name = profile.first_name or ""
    last_name = profile.last_name or ""
    full_name = _display_name(first_name, last_name, email)

    if user is None:
        user = User(
            email=email,
            email_verified_at=utcnow(),
            password_hash=hash_password(secrets.token_urlsafe(32)),
            full_name=full_name,
            workos_user_id=profile.id,
            auth_provider="workos_sso",
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()
    else:
        if user.status != UserStatus.ACTIVE:
            raise ForbiddenError("This user account is disabled.")
        if user.workos_user_id is None:
            user.workos_user_id = profile.id
        user.auth_provider = "workos_sso"
        if not user.full_name:
            user.full_name = full_name

    membership = await session.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant.id,
            Membership.user_id == user.id,
        )
    )
    if membership is None:
        existing_members = int(
            await session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(Membership.tenant_id == tenant.id)
            )
            or 0
        )
        membership = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=MemberRole.OWNER if existing_members == 0 else _mapped_role(profile),
            status=MembershipStatus.ACTIVE,
        )
        session.add(membership)
        await session.flush()
    elif membership.status != MembershipStatus.ACTIVE:
        raise ForbiddenError("Your workspace membership is not active.")

    user.last_login_at = utcnow()
    await audit_service.record(
        session,
        tenant_id=tenant.id,
        action="auth.sso_login",
        actor_type="user",
        actor_id=user.id,
        entity_type="user",
        entity_id=user.id,
        after_data={
            "provider": "workos",
            "connection_id": profile.connection_id,
            "organization_id": profile.organization_id,
        },
    )
    await session.commit()
    return Principal(user=user, tenant=tenant, membership=membership)
