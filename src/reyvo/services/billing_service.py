from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.config import get_settings
from reyvo.core.exceptions import BadRequestError, NotFoundError
from reyvo.core.tenancy import Principal
from reyvo.core.utils import utcnow
from reyvo.db.enums import SubscriptionStatus, TenantStatus
from reyvo.db.models import Plan, Subscription, Tenant
from reyvo.integrations.billing import stripe_gateway
from reyvo.schemas.billing import CheckoutRequest, CheckoutResponse

DEFAULT_PLANS = [
    {
        "code": "trial",
        "name": "Trial",
        "limits": {"contacts": 250, "sending_inboxes": 1, "daily_send_cap": 50},
    },
    {
        "code": "starter",
        "name": "Starter",
        "limits": {"contacts": 2_500, "sending_inboxes": 2, "daily_send_cap": 100},
    },
    {
        "code": "growth",
        "name": "Growth",
        "limits": {"contacts": 25_000, "sending_inboxes": 8, "daily_send_cap": 500},
    },
    {
        "code": "scale",
        "name": "Scale",
        "limits": {"contacts": 100_000, "sending_inboxes": 25, "daily_send_cap": 2_000},
    },
]


async def ensure_default_plans(session: AsyncSession) -> None:
    existing = await session.scalar(select(Plan.code))
    if existing is not None:
        return
    settings = get_settings()
    prices = {
        "starter": settings.stripe_price_starter,
        "growth": settings.stripe_price_growth,
        "scale": settings.stripe_price_scale,
    }
    for item in DEFAULT_PLANS:
        session.add(
            Plan(
                code=item["code"],
                name=item["name"],
                limits=item["limits"],
                stripe_price_id=prices.get(item["code"]),
                active=True,
            )
        )
    await session.commit()


async def list_plans(session: AsyncSession) -> list[Plan]:
    stmt = select(Plan).where(Plan.active.is_(True)).order_by(Plan.code)
    plans = list((await session.scalars(stmt)).all())
    if not plans:
        await ensure_default_plans(session)
        plans = list((await session.scalars(stmt)).all())
    return plans


async def get_subscription(session: AsyncSession, tenant_id: uuid.UUID) -> Subscription:
    subscription = await session.scalar(
        select(Subscription).where(Subscription.tenant_id == tenant_id)
    )
    if subscription is None:
        subscription = Subscription(
            tenant_id=tenant_id,
            plan_code="trial",
            status=SubscriptionStatus.TRIALING,
            trial_ends_at=utcnow() + timedelta(days=14),
        )
        session.add(subscription)
        await session.commit()
        await session.refresh(subscription)
    return subscription


async def create_checkout(
    session: AsyncSession, principal: Principal, payload: CheckoutRequest
) -> CheckoutResponse:
    settings = get_settings()
    subscription = await get_subscription(session, principal.tenant_id)
    base_url = settings.app_public_url.rstrip("/")
    success_url = payload.success_url or f"{base_url}/billing?checkout=success"
    cancel_url = payload.cancel_url or f"{base_url}/billing?checkout=cancelled"

    result = stripe_gateway.create_checkout_session(
        tenant_id=str(principal.tenant_id),
        customer_email=principal.user.email,
        plan_code=payload.plan_code,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    if not subscription.stripe_customer_id:
        # The customer is created by Stripe asynchronously; the webhook reconciles it.
        pass
    return CheckoutResponse(checkout_url=result["checkout_url"], session_id=result["session_id"])


async def create_portal(
    session: AsyncSession, principal: Principal, *, return_url: str | None = None
) -> str:
    settings = get_settings()
    subscription = await get_subscription(session, principal.tenant_id)
    if not subscription.stripe_customer_id:
        raise BadRequestError("This workspace does not have a billing account yet.")
    url = stripe_gateway.create_portal_session(
        customer_id=subscription.stripe_customer_id,
        return_url=return_url or f"{settings.app_public_url.rstrip('/')}/billing",
    )
    return url


async def handle_webhook_event(session: AsyncSession, event: dict[str, Any]) -> None:
    event_type = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}

    if event_type == "checkout.session.completed":
        metadata = data.get("metadata") or {}
        tenant_id = metadata.get("tenant_id") or data.get("client_reference_id")
        if not tenant_id:
            return
        subscription = await get_subscription(session, uuid.UUID(str(tenant_id)))
        subscription.stripe_customer_id = data.get("customer") or subscription.stripe_customer_id
        subscription.stripe_subscription_id = (
            data.get("subscription") or subscription.stripe_subscription_id
        )
        subscription.plan_code = metadata.get("plan_code", subscription.plan_code)
        subscription.status = SubscriptionStatus.ACTIVE
        tenant = await session.get(Tenant, subscription.tenant_id)
        if tenant is not None:
            tenant.plan_code = subscription.plan_code
            if tenant.status == TenantStatus.TRIAL:
                tenant.status = TenantStatus.ACTIVE
        await session.commit()
        return

    if event_type in {
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.payment_failed",
    }:
        stripe_subscription_id = data.get("subscription") or data.get("id")
        customer_id = data.get("customer")
        stmt = select(Subscription)
        if stripe_subscription_id:
            stmt = stmt.where(Subscription.stripe_subscription_id == stripe_subscription_id)
        elif customer_id:
            stmt = stmt.where(Subscription.stripe_customer_id == customer_id)
        else:
            return
        subscription = await session.scalar(stmt)
        if subscription is None:
            return

        if event_type == "customer.subscription.deleted":
            subscription.status = SubscriptionStatus.CANCELED
        elif event_type == "invoice.payment_failed":
            subscription.status = SubscriptionStatus.PAST_DUE
        else:
            raw_status = data.get("status", "active")
            try:
                subscription.status = SubscriptionStatus(raw_status)
            except ValueError:
                subscription.status = SubscriptionStatus.ACTIVE
            if data.get("current_period_start"):
                subscription.current_period_start = datetime.fromtimestamp(
                    int(data["current_period_start"]), tz=UTC
                )
            if data.get("current_period_end"):
                subscription.current_period_end = datetime.fromtimestamp(
                    int(data["current_period_end"]), tz=UTC
                )
            subscription.cancel_at_period_end = bool(data.get("cancel_at_period_end"))

        tenant = await session.get(Tenant, subscription.tenant_id)
        if tenant is not None:
            tenant.status = (
                TenantStatus.PAST_DUE
                if subscription.status == SubscriptionStatus.PAST_DUE
                else TenantStatus.ACTIVE
            )
        await session.commit()
        return


async def get_tenant_for_subscription(session: AsyncSession, subscription: Subscription) -> Tenant:
    tenant = await session.get(Tenant, subscription.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found.")
    return tenant
