from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.exceptions import ConflictError, NotFoundError, UnauthorizedError
from reyvo.core.pagination import Pagination
from reyvo.core.security import verify_unsubscribe_token
from reyvo.core.tenancy import Principal
from reyvo.core.utils import mask_email, normalize_email, utcnow
from reyvo.db.enums import ContactStatus, SuppressionReason
from reyvo.db.models import Contact, Suppression, Tenant
from reyvo.schemas.suppression import SuppressionCreate, UnsubscribeInfo, UnsubscribeResponse
from reyvo.services import audit_service


def _tenant_condition(tenant_id: uuid.UUID | None):
    if tenant_id is None:
        return Suppression.tenant_id.is_(None)
    return or_(Suppression.tenant_id == tenant_id, Suppression.tenant_id.is_(None))


async def is_suppressed(session: AsyncSession, tenant_id: uuid.UUID, email: str) -> bool:
    normalized = normalize_email(email)
    found = await session.scalar(
        select(Suppression.id).where(
            _tenant_condition(tenant_id),
            Suppression.email_normalized == normalized,
        )
    )
    return found is not None


async def list_suppressions(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pagination: Pagination,
    *,
    reason: SuppressionReason | None = None,
) -> tuple[list[Suppression], int]:
    stmt = select(Suppression).where(_tenant_condition(tenant_id))
    count_stmt = select(func.count()).select_from(Suppression).where(_tenant_condition(tenant_id))
    if reason is not None:
        stmt = stmt.where(Suppression.reason == reason)
        count_stmt = count_stmt.where(Suppression.reason == reason)

    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(Suppression.created_at.desc())
        .limit(pagination.capped_limit)
        .offset(pagination.offset)
    )
    return list((await session.scalars(stmt)).all()), total


async def add_suppression(
    session: AsyncSession,
    principal: Principal,
    payload: SuppressionCreate,
    *,
    commit: bool = True,
) -> Suppression:
    email = normalize_email(str(payload.email))
    existing = await session.scalar(
        select(Suppression).where(
            _tenant_condition(principal.tenant_id),
            Suppression.email_normalized == email,
        )
    )
    if existing is not None:
        raise ConflictError("This email address is already suppressed.")

    suppression = Suppression(
        tenant_id=principal.tenant_id,
        email_normalized=email,
        reason=payload.reason,
        source="api",
        note=payload.note,
        created_by=principal.user_id,
    )
    session.add(suppression)

    contact = await session.scalar(
        select(Contact).where(
            Contact.tenant_id == principal.tenant_id,
            Contact.email_normalized == email,
        )
    )
    if contact is not None:
        contact.status = ContactStatus.UNSUBSCRIBED

    await session.flush()
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="suppression.created",
        actor_id=principal.user_id,
        entity_type="suppression",
        entity_id=suppression.id,
        after_data={"email": email, "reason": payload.reason.value},
    )
    if commit:
        await session.commit()
        await session.refresh(suppression)
    return suppression


async def remove_suppression(
    session: AsyncSession, principal: Principal, suppression_id: uuid.UUID
) -> None:
    suppression = await session.scalar(
        select(Suppression).where(
            Suppression.id == suppression_id,
            Suppression.tenant_id == principal.tenant_id,
        )
    )
    if suppression is None:
        raise NotFoundError("Suppression not found.")
    await session.delete(suppression)
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="suppression.removed",
        actor_id=principal.user_id,
        entity_type="suppression",
        entity_id=suppression_id,
        before_data={"email": suppression.email_normalized, "reason": suppression.reason.value},
    )
    await session.commit()


async def get_unsubscribe_info(session: AsyncSession, token: str) -> UnsubscribeInfo:
    data = verify_unsubscribe_token(token)
    try:
        tenant_id = uuid.UUID(data["tid"])
        contact_id = uuid.UUID(data["cid"])
    except ValueError as exc:
        raise UnauthorizedError("This unsubscribe link is not valid.") from exc

    tenant = await session.get(Tenant, tenant_id)
    contact = await session.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
    )
    if tenant is None or contact is None:
        raise NotFoundError("This unsubscribe link is no longer valid.")

    return UnsubscribeInfo(
        valid=True,
        tenant_name=tenant.name,
        masked_email=mask_email(contact.email_normalized),
    )


async def unsubscribe(
    session: AsyncSession, token: str, *, reason: str | None = None
) -> UnsubscribeResponse:
    data = verify_unsubscribe_token(token)
    try:
        tenant_id = uuid.UUID(data["tid"])
        contact_id = uuid.UUID(data["cid"])
    except ValueError as exc:
        raise UnauthorizedError("This unsubscribe link is not valid.") from exc

    contact = await session.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
    )
    if contact is None:
        raise NotFoundError("This unsubscribe link is no longer valid.")

    existing = await session.scalar(
        select(Suppression).where(
            Suppression.tenant_id == tenant_id,
            Suppression.email_normalized == contact.email_normalized,
        )
    )
    already = existing is not None
    if existing is None:
        session.add(
            Suppression(
                tenant_id=tenant_id,
                email_normalized=contact.email_normalized,
                reason=SuppressionReason.UNSUBSCRIBE,
                source="unsubscribe_link",
                note=(reason or "")[:500] or None,
            )
        )
    contact.status = ContactStatus.UNSUBSCRIBED
    contact.last_replied_at = contact.last_replied_at or utcnow()
    await audit_service.record(
        session,
        tenant_id=tenant_id,
        action="suppression.public_unsubscribe",
        actor_type="contact",
        actor_id=contact.id,
        entity_type="contact",
        entity_id=contact.id,
        after_data={"reason": (reason or "unsubscribe")[:120]},
    )
    await session.commit()
    return UnsubscribeResponse(
        message="You have been unsubscribed and will not receive further emails.",
        already_suppressed=already,
    )
