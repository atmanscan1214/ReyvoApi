from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_pagination, get_principal, require_roles
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.enums import CampaignStatus, EnrollmentStatus, MemberRole
from reyvo.db.session import get_db
from reyvo.schemas.campaign import (
    CampaignCreate,
    CampaignPreflight,
    CampaignPreviewRequest,
    CampaignPreviewResponse,
    CampaignRead,
    CampaignRejectRequest,
    CampaignStats,
    CampaignUpdate,
    EnrollmentRead,
)
from reyvo.schemas.common import Page
from reyvo.services import campaign_service

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("", response_model=Page[CampaignRead])
async def list_campaigns(
    status_filter: CampaignStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=255),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Page[CampaignRead]:
    items, total = await campaign_service.list_campaigns(
        session,
        principal.tenant_id,
        pagination,
        status=status_filter,
        search=search,
    )
    return Page[CampaignRead](
        items=[CampaignRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@router.post("", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignCreate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CampaignRead:
    campaign = await campaign_service.create_campaign(session, principal, payload)
    return CampaignRead.model_validate(campaign)


@router.get("/{campaign_id}", response_model=CampaignRead)
async def get_campaign(
    campaign_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CampaignRead:
    campaign = await campaign_service.get_campaign_or_404(session, principal.tenant_id, campaign_id)
    return CampaignRead.model_validate(campaign)


@router.patch("/{campaign_id}", response_model=CampaignRead)
async def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CampaignRead:
    campaign = await campaign_service.update_campaign(session, principal, campaign_id, payload)
    return CampaignRead.model_validate(campaign)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(
    campaign_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    await campaign_service.delete_campaign(session, principal, campaign_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{campaign_id}/submit", response_model=CampaignRead)
async def submit_campaign(
    campaign_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CampaignRead:
    campaign = await campaign_service.submit_campaign(session, principal, campaign_id)
    return CampaignRead.model_validate(campaign)


@router.post("/{campaign_id}/approve", response_model=CampaignRead)
async def approve_campaign(
    campaign_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(MemberRole.OWNER, MemberRole.ADMIN)),
) -> CampaignRead:
    campaign = await campaign_service.approve_campaign(session, principal, campaign_id)
    return CampaignRead.model_validate(campaign)


@router.post("/{campaign_id}/reject", response_model=CampaignRead)
async def reject_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignRejectRequest | None = None,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(MemberRole.OWNER, MemberRole.ADMIN)),
) -> CampaignRead:
    campaign = await campaign_service.reject_campaign(
        session, principal, campaign_id, note=payload.note if payload else None
    )
    return CampaignRead.model_validate(campaign)


@router.post("/{campaign_id}/launch", response_model=CampaignRead)
async def launch_campaign(
    campaign_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(MemberRole.OWNER, MemberRole.ADMIN)),
) -> CampaignRead:
    campaign = await campaign_service.launch_campaign(session, principal, campaign_id)
    return CampaignRead.model_validate(campaign)


@router.post("/{campaign_id}/pause", response_model=CampaignRead)
async def pause_campaign(
    campaign_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CampaignRead:
    campaign = await campaign_service.set_campaign_status(
        session, principal, campaign_id, action="pause"
    )
    return CampaignRead.model_validate(campaign)


@router.post("/{campaign_id}/resume", response_model=CampaignRead)
async def resume_campaign(
    campaign_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CampaignRead:
    campaign = await campaign_service.set_campaign_status(
        session, principal, campaign_id, action="resume"
    )
    return CampaignRead.model_validate(campaign)


@router.post("/{campaign_id}/archive", response_model=CampaignRead)
async def archive_campaign(
    campaign_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CampaignRead:
    campaign = await campaign_service.set_campaign_status(
        session, principal, campaign_id, action="archive"
    )
    return CampaignRead.model_validate(campaign)


@router.get("/{campaign_id}/preflight", response_model=CampaignPreflight)
async def campaign_preflight(
    campaign_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CampaignPreflight:
    campaign = await campaign_service.get_campaign_or_404(session, principal.tenant_id, campaign_id)
    result = await campaign_service.preflight(session, campaign)
    return CampaignPreflight(**result)


@router.post("/{campaign_id}/preview", response_model=CampaignPreviewResponse)
async def preview_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignPreviewRequest,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CampaignPreviewResponse:
    return await campaign_service.preview_campaign_step(
        session,
        principal,
        campaign_id,
        contact_id=payload.contact_id,
        step_order=payload.step_order,
    )


@router.get("/{campaign_id}/stats", response_model=CampaignStats)
async def campaign_stats(
    campaign_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CampaignStats:
    return await campaign_service.campaign_stats(session, principal.tenant_id, campaign_id)


@router.get("/{campaign_id}/enrollments", response_model=Page[EnrollmentRead])
async def list_enrollments(
    campaign_id: uuid.UUID,
    status_filter: EnrollmentStatus | None = Query(default=None, alias="status"),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Page[EnrollmentRead]:
    items, total = await campaign_service.list_enrollments(
        session,
        principal.tenant_id,
        campaign_id,
        pagination,
        status=status_filter,
    )
    return Page[EnrollmentRead](
        items=[EnrollmentRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )
