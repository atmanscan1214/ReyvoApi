from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.exceptions import BadRequestError, NotFoundError
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.enums import OpportunityStage
from reyvo.db.models import Company, Contact, Membership, Opportunity
from reyvo.schemas.opportunity import (
    OpportunityCreate,
    OpportunityUpdate,
    PipelineSummary,
)
from reyvo.services import audit_service


async def list_opportunities(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pagination: Pagination,
    *,
    stage: OpportunityStage | None = None,
    owner_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
) -> tuple[list[Opportunity], int]:
    stmt = select(Opportunity).where(Opportunity.tenant_id == tenant_id)
    count_stmt = (
        select(func.count()).select_from(Opportunity).where(Opportunity.tenant_id == tenant_id)
    )
    if stage is not None:
        stmt = stmt.where(Opportunity.stage == stage)
        count_stmt = count_stmt.where(Opportunity.stage == stage)
    if owner_id is not None:
        stmt = stmt.where(Opportunity.owner_id == owner_id)
        count_stmt = count_stmt.where(Opportunity.owner_id == owner_id)
    if contact_id is not None:
        stmt = stmt.where(Opportunity.contact_id == contact_id)
        count_stmt = count_stmt.where(Opportunity.contact_id == contact_id)

    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(Opportunity.created_at.desc())
        .limit(pagination.capped_limit)
        .offset(pagination.offset)
    )
    return list((await session.scalars(stmt)).all()), total


async def get_opportunity_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, opportunity_id: uuid.UUID
) -> Opportunity:
    opportunity = await session.scalar(
        select(Opportunity).where(
            Opportunity.id == opportunity_id,
            Opportunity.tenant_id == tenant_id,
        )
    )
    if opportunity is None:
        raise NotFoundError("Opportunity not found.")
    return opportunity


async def _validate_links(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    contact_id: uuid.UUID | None,
    company_id: uuid.UUID | None,
    owner_id: uuid.UUID | None,
) -> None:
    if contact_id is not None:
        exists = await session.scalar(
            select(Contact.id).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
        )
        if exists is None:
            raise BadRequestError("The selected contact does not exist in this workspace.")
    if company_id is not None:
        exists = await session.scalar(
            select(Company.id).where(Company.id == company_id, Company.tenant_id == tenant_id)
        )
        if exists is None:
            raise BadRequestError("The selected company does not exist in this workspace.")
    if owner_id is not None:
        exists = await session.scalar(
            select(Membership.id).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == owner_id,
            )
        )
        if exists is None:
            raise BadRequestError("The selected owner is not a member of this workspace.")


async def create_opportunity(
    session: AsyncSession, principal: Principal, payload: OpportunityCreate
) -> Opportunity:
    await _validate_links(
        session,
        principal.tenant_id,
        contact_id=payload.contact_id,
        company_id=payload.company_id,
        owner_id=payload.owner_id,
    )
    opportunity = Opportunity(
        tenant_id=principal.tenant_id,
        company_id=payload.company_id,
        contact_id=payload.contact_id,
        stage=payload.stage,
        value_cents=payload.value_cents,
        currency=payload.currency.upper(),
        owner_id=payload.owner_id or principal.user_id,
        source_campaign_id=payload.source_campaign_id,
        expected_close_date=payload.expected_close_date,
        notes=payload.notes,
    )
    session.add(opportunity)
    await session.flush()
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="opportunity.created",
        actor_id=principal.user_id,
        entity_type="opportunity",
        entity_id=opportunity.id,
        after_data={"stage": opportunity.stage.value, "value_cents": opportunity.value_cents},
    )
    await session.commit()
    await session.refresh(opportunity)
    return opportunity


async def update_opportunity(
    session: AsyncSession,
    principal: Principal,
    opportunity_id: uuid.UUID,
    payload: OpportunityUpdate,
) -> Opportunity:
    opportunity = await get_opportunity_or_404(session, principal.tenant_id, opportunity_id)
    data = payload.model_dump(exclude_unset=True)
    await _validate_links(
        session,
        principal.tenant_id,
        contact_id=None,
        company_id=None,
        owner_id=data.get("owner_id"),
    )
    before = {"stage": opportunity.stage.value, "value_cents": opportunity.value_cents}
    for field, value in data.items():
        if field == "currency" and value:
            value = value.upper()
        setattr(opportunity, field, value)

    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="opportunity.updated",
        actor_id=principal.user_id,
        entity_type="opportunity",
        entity_id=opportunity.id,
        before_data=before,
        after_data={"stage": opportunity.stage.value},
    )
    await session.commit()
    await session.refresh(opportunity)
    return opportunity


async def delete_opportunity(
    session: AsyncSession, principal: Principal, opportunity_id: uuid.UUID
) -> None:
    opportunity = await get_opportunity_or_404(session, principal.tenant_id, opportunity_id)
    await session.delete(opportunity)
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="opportunity.deleted",
        actor_id=principal.user_id,
        entity_type="opportunity",
        entity_id=opportunity_id,
    )
    await session.commit()


async def pipeline_summary(session: AsyncSession, tenant_id: uuid.UUID) -> list[PipelineSummary]:
    stmt = (
        select(Opportunity.stage, func.count(), func.coalesce(func.sum(Opportunity.value_cents), 0))
        .where(Opportunity.tenant_id == tenant_id)
        .group_by(Opportunity.stage)
    )
    result = {row[0]: (int(row[1]), int(row[2])) for row in (await session.execute(stmt)).all()}
    return [
        PipelineSummary(
            stage=stage,
            count=result.get(stage, (0, 0))[0],
            value_cents=result.get(stage, (0, 0))[1],
        )
        for stage in OpportunityStage
    ]
