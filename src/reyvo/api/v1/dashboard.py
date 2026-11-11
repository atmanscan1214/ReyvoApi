from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_principal
from reyvo.core.tenancy import Principal
from reyvo.db.session import get_db
from reyvo.schemas.dashboard import DashboardSummary
from reyvo.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def summary(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> DashboardSummary:
    return await dashboard_service.summary(session, principal.tenant_id)
