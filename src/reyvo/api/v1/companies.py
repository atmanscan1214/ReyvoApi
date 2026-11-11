from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_pagination, get_principal
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.session import get_db
from reyvo.schemas.common import Page
from reyvo.schemas.contact import CompanyCreate, CompanyRead, CompanyUpdate
from reyvo.services import company_service

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=Page[CompanyRead])
async def list_companies(
    search: str | None = Query(default=None, max_length=255),
    industry: str | None = Query(default=None, max_length=120),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Page[CompanyRead]:
    items, total = await company_service.list_companies(
        session, principal.tenant_id, pagination, search=search, industry=industry
    )
    return Page[CompanyRead](
        items=[CompanyRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CompanyCreate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CompanyRead:
    company = await company_service.create_company(session, principal, payload)
    return CompanyRead.model_validate(company)


@router.get("/{company_id}", response_model=CompanyRead)
async def get_company(
    company_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CompanyRead:
    company = await company_service.get_company_or_404(session, principal.tenant_id, company_id)
    return CompanyRead.model_validate(company)


@router.patch("/{company_id}", response_model=CompanyRead)
async def update_company(
    company_id: uuid.UUID,
    payload: CompanyUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CompanyRead:
    company = await company_service.update_company(session, principal, company_id, payload)
    return CompanyRead.model_validate(company)


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    await company_service.delete_company(session, principal, company_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
