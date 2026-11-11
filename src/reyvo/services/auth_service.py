from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from reyvo.core.config import get_settings
from reyvo.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    UnauthorizedError,
)
from reyvo.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from reyvo.core.tenancy import Principal
from reyvo.core.utils import normalize_email, unique_slug, utcnow
from reyvo.db.enums import (
    MemberRole,
    MembershipStatus,
    SubscriptionStatus,
    TenantStatus,
    UserStatus,
)
from reyvo.db.models import Membership, Subscription, Tenant, User
from reyvo.schemas.auth import LoginRequest, RegisterRequest, TokenPair


def _ensure_tenant_usable(tenant: Tenant) -> None:
    if tenant.status in {TenantStatus.SUSPENDED, TenantStatus.DELETED, TenantStatus.CANCELED}:
        raise ForbiddenError("This workspace is not available. Contact your administrator.")


def _ensure_user_active(user: User) -> None:
    if user.status != UserStatus.ACTIVE:
        raise ForbiddenError("This user account is disabled.")


def build_token_pair(principal: Principal) -> TokenPair:
    settings = get_settings()
    access_token = create_access_token(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        role=principal.role,
    )
    refresh_token = create_refresh_token(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
        tenant={
            "id": principal.tenant.id,
            "name": principal.tenant.name,
            "slug": principal.tenant.slug,
        },
    )


async def register(session: AsyncSession, payload: RegisterRequest) -> Principal:
    email = normalize_email(payload.email)
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise ConflictError("An account with this email already exists.")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()

    tenant = Tenant(
        name=payload.agency_name.strip(),
        slug=unique_slug(payload.agency_name),
        status=TenantStatus.TRIAL,
        plan_code="trial",
        timezone=payload.timezone,
        physical_address=payload.physical_address,
    )
    session.add(tenant)
    await session.flush()

    membership = Membership(
        tenant_id=tenant.id,
        user_id=user.id,
        role=MemberRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    session.add(membership)

    trial_end = utcnow() + timedelta(days=14)
    tenant.trial_ends_at = trial_end
    session.add(
        Subscription(
            tenant_id=tenant.id,
            plan_code="trial",
            status=SubscriptionStatus.TRIALING,
            trial_ends_at=trial_end,
        )
    )
    await session.commit()
    await session.refresh(user)
    await session.refresh(tenant)
    await session.refresh(membership)
    return Principal(user=user, tenant=tenant, membership=membership)


async def authenticate(session: AsyncSession, payload: LoginRequest) -> Principal:
    email = normalize_email(payload.email)
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise UnauthorizedError("Invalid email or password.")
    _ensure_user_active(user)

    stmt = (
        select(Membership)
        .options(selectinload(Membership.user), selectinload(Membership.tenant))
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(
            Membership.user_id == user.id,
            Membership.status == MembershipStatus.ACTIVE,
        )
    )
    if payload.tenant_slug:
        stmt = stmt.where(Tenant.slug == payload.tenant_slug.strip().lower())
    memberships = list((await session.scalars(stmt)).all())

    if not memberships:
        raise ForbiddenError("No active workspace membership was found for this account.")
    if len(memberships) > 1 and not payload.tenant_slug:
        raise BadRequestError(
            "This account belongs to multiple workspaces. Provide tenant_slug to sign in."
        )

    membership = memberships[0]
    tenant = membership.tenant
    _ensure_tenant_usable(tenant)

    user.last_login_at = utcnow()
    await session.commit()
    return Principal(user=user, tenant=tenant, membership=membership)


async def principal_from_access_token(session: AsyncSession, token: str) -> Principal:
    claims = decode_token(token, expected_type="access")
    try:
        user_id = uuid.UUID(str(claims["sub"]))
        tenant_id = uuid.UUID(str(claims["tid"]))
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("The access token is malformed.") from exc

    stmt = (
        select(Membership)
        .options(selectinload(Membership.user), selectinload(Membership.tenant))
        .where(Membership.user_id == user_id, Membership.tenant_id == tenant_id)
    )
    membership = await session.scalar(stmt)
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise ForbiddenError("Your workspace membership is no longer active.")

    _ensure_user_active(membership.user)
    _ensure_tenant_usable(membership.tenant)
    return Principal(user=membership.user, tenant=membership.tenant, membership=membership)


async def refresh(
    session: AsyncSession, *, refresh_token: str, tenant_id: uuid.UUID | None = None
) -> Principal:
    claims = decode_token(refresh_token, expected_type="refresh")
    try:
        user_id = uuid.UUID(str(claims["sub"]))
        token_tenant_id = uuid.UUID(str(claims["tid"]))
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("The refresh token is malformed.") from exc

    if tenant_id is not None and tenant_id != token_tenant_id:
        raise UnauthorizedError("The refresh token does not belong to this workspace.")

    stmt = (
        select(Membership)
        .options(selectinload(Membership.user), selectinload(Membership.tenant))
        .where(
            Membership.user_id == user_id,
            Membership.tenant_id == token_tenant_id,
        )
    )
    membership = await session.scalar(stmt)
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise ForbiddenError("Your workspace membership is no longer active.")

    _ensure_user_active(membership.user)
    _ensure_tenant_usable(membership.tenant)
    return Principal(user=membership.user, tenant=membership.tenant, membership=membership)
