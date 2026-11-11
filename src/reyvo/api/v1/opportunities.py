from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_pagination, get_principal
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.enums import OpportunityStage
from reyvo.db.session import get_db
from reyvo.schemas.common import Page
from reyvo.schemas.opportunity import (
    OpportunityCreate,
    OpportunityRead,
    OpportunityUpdate,
    PipelineSummary,
)
from reyvo.services import opportunity_service

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("", response_model=Page[OpportunityRead])
async def list_opportunities(
    stage: OpportunityStage | None = Query(default=None),
    owner_id: uuid.UUID | None = Query(default=None),
    contact_id: uuid.UUID | None = Query(default=None),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Page[OpportunityRead]:
    items, total = await opportunity_service.list_opportunities(
        session,
        principal.tenant_id,
        pagination,
        stage=stage,
        owner_id=owner_id,
        contact_id=contact_id,
    )
    return Page[OpportunityRead](
        items=[OpportunityRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@router.get("/pipeline", response_model=list[PipelineSummary])
async def pipeline(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[PipelineSummary]:
    return await opportunity_service.pipeline_summary(session, principal.tenant_id)


@router.post("", response_model=OpportunityRead, status_code=status.HTTP_201_CREATED)
async def create_opportunity(
    payload: OpportunityCreate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> OpportunityRead:
    opportunity = await opportunity_service.create_opportunity(session, principal, payload)
    return OpportunityRead.model_validate(opportunity)


@router.get("/{opportunity_id}", response_model=OpportunityRead)
async def get_opportunity(
    opportunity_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> OpportunityRead:
    opportunity = await opportunity_service.get_opportunity_or_404(
        session, principal.tenant_id, opportunity_id
    )
    return OpportunityRead.model_validate(opportunity)


@router.patch("/{opportunity_id}", response_model=OpportunityRead)
async def update_opportunity(
    opportunity_id: uuid.UUID,
    payload: OpportunityUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> OpportunityRead:
    opportunity = await opportunity_service.update_opportunity(
        session, principal, opportunity_id, payload
    )
    return OpportunityRead.model_validate(opportunity)


@router.delete("/{opportunity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_opportunity(
    opportunity_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    await opportunity_service.delete_opportunity(session, principal, opportunity_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
