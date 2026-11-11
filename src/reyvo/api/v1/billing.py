from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_principal, require_roles
from reyvo.core.tenancy import Principal
from reyvo.db.enums import MemberRole
from reyvo.db.session import get_db
from reyvo.schemas.billing import (
    BillingPortalResponse,
    CheckoutRequest,
    CheckoutResponse,
    PlanRead,
    SubscriptionRead,
)
from reyvo.services import billing_service

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanRead])
async def list_plans(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[PlanRead]:
    plans = await billing_service.list_plans(session)
    return [PlanRead.model_validate(plan) for plan in plans]


@router.get("/subscription", response_model=SubscriptionRead)
async def get_subscription(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> SubscriptionRead:
    subscription = await billing_service.get_subscription(session, principal.tenant_id)
    return SubscriptionRead.model_validate(subscription)


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    payload: CheckoutRequest,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(MemberRole.OWNER, MemberRole.ADMIN)),
) -> CheckoutResponse:
    return await billing_service.create_checkout(session, principal, payload)


@router.post("/portal", response_model=BillingPortalResponse)
async def create_portal(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(MemberRole.OWNER, MemberRole.ADMIN)),
) -> BillingPortalResponse:
    url = await billing_service.create_portal(session, principal)
    return BillingPortalResponse(url=url)
