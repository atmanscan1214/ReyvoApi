from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_pagination, get_principal
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.enums import SuppressionReason
from reyvo.db.session import get_db
from reyvo.schemas.common import Page
from reyvo.schemas.suppression import SuppressionCreate, SuppressionRead
from reyvo.services import suppression_service

router = APIRouter(prefix="/suppressions", tags=["suppressions"])


@router.get("", response_model=Page[SuppressionRead])
async def list_suppressions(
    reason: SuppressionReason | None = Query(default=None),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Page[SuppressionRead]:
    items, total = await suppression_service.list_suppressions(
        session, principal.tenant_id, pagination, reason=reason
    )
    return Page[SuppressionRead](
        items=[SuppressionRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@router.post("", response_model=SuppressionRead, status_code=status.HTTP_201_CREATED)
async def add_suppression(
    payload: SuppressionCreate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> SuppressionRead:
    suppression = await suppression_service.add_suppression(session, principal, payload)
    return SuppressionRead.model_validate(suppression)


@router.delete("/{suppression_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_suppression(
    suppression_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    await suppression_service.remove_suppression(session, principal, suppression_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
