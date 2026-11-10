from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.exceptions import BadRequestError, NotFoundError
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.core.utils import utcnow
from reyvo.db.enums import CampaignStatus, MemberRole, MembershipStatus, MessageDirection
from reyvo.db.models import (
    AuditLog,
    Campaign,
    Company,
    Contact,
    EmailAccount,
    Membership,
    Message,
    Reply,
    Tenant,
    User,
)
from reyvo.schemas.tenant import MemberRead, TenantUpdate, UsageRead
from reyvo.services import audit_service


async def update_tenant(
    session: AsyncSession,
    principal: Principal,
    payload: TenantUpdate,
) -> Tenant:
    tenant = principal.tenant
    before = {"name": tenant.name, "timezone": tenant.timezone}
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(tenant, field, value)
    await audit_service.record(
        session,
        tenant_id=tenant.id,
        action="tenant.updated",
        actor_id=principal.user_id,
        entity_type="tenant",
        entity_id=tenant.id,
        before_data=before,
        after_data=data,
    )
    await session.commit()
    await session.refresh(tenant)
    return tenant


async def list_members(session: AsyncSession, tenant_id: uuid.UUID) -> list[MemberRead]:
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == tenant_id)
        .order_by(User.full_name)
    )
    rows = (await session.execute(stmt)).all()
    return [
        MemberRead(
            membership_id=membership.id,
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=membership.role,
            status=membership.status.value,
            created_at=membership.created_at,
        )
        for membership, user in rows
    ]


async def update_member_role(
    session: AsyncSession,
    principal: Principal,
    membership_id: uuid.UUID,
    role: MemberRole,
) -> MemberRead:
    membership = await session.scalar(
        select(Membership).where(
            Membership.id == membership_id,
            Membership.tenant_id == principal.tenant_id,
        )
    )
    if membership is None:
        raise NotFoundError("Team member not found.")

    if membership.role == MemberRole.OWNER and role != MemberRole.OWNER:
        owner_count = await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.tenant_id == principal.tenant_id,
                Membership.role == MemberRole.OWNER,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
        if (owner_count or 0) <= 1:
            raise BadRequestError("A workspace must keep at least one owner.")

    before = {"role": membership.role.value}
    membership.role = role
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="membership.role_changed",
        actor_id=principal.user_id,
        entity_type="membership",
        entity_id=membership.id,
        before_data=before,
        after_data={"role": role.value},
    )
    await session.commit()

    user = await session.get(User, membership.user_id)
    assert user is not None
    return MemberRead(
        membership_id=membership.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=membership.role,
        status=membership.status.value,
        created_at=membership.created_at,
    )


async def usage(session: AsyncSession, principal: Principal) -> UsageRead:
    tenant_id = principal.tenant_id

    async def count(model, *conditions) -> int:
        stmt = (
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id, *conditions)
        )
        return int(await session.scalar(stmt) or 0)

    contacts = await count(Contact)
    companies = await count(Company)
    campaigns = await count(Campaign)
    active_campaigns = await count(Campaign, Campaign.status == CampaignStatus.ACTIVE)
    messages_sent = await count(Message, Message.direction == MessageDirection.OUTBOUND)
    replies = await count(Reply)
    seats = int(
        await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
        or 0
    )
    daily_cap = int(
        await session.scalar(
            select(func.coalesce(func.sum(EmailAccount.daily_cap), 0)).where(
                EmailAccount.tenant_id == tenant_id
            )
        )
        or 0
    )

    return UsageRead(
        contacts=contacts,
        companies=companies,
        campaigns=campaigns,
        active_campaigns=active_campaigns,
        messages_sent=messages_sent,
        replies=replies,
        seats=seats,
        daily_send_cap=daily_cap,
    )


async def list_audit_logs(
    session: AsyncSession,
    principal: Principal,
    pagination: Pagination,
    *,
    action: str | None = None,
) -> tuple[list[AuditLog], int]:
    stmt = select(AuditLog).where(AuditLog.tenant_id == principal.tenant_id)
    count_stmt = (
        select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == principal.tenant_id)
    )
    if action:
        stmt = stmt.where(AuditLog.action == action)
        count_stmt = count_stmt.where(AuditLog.action == action)

    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(AuditLog.created_at.desc())
        .limit(pagination.capped_limit)
        .offset(pagination.offset)
    )
    entries = list((await session.scalars(stmt)).all())
    return entries, total


def trial_days_remaining(tenant: Tenant) -> int | None:
    if tenant.trial_ends_at is None:
        return None
    delta = tenant.trial_ends_at - utcnow()
    return max(0, delta.days)
