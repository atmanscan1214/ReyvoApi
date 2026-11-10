from __future__ import annotations

from typing import Any

import stripe

from reyvo.core.config import get_settings
from reyvo.core.exceptions import ServiceUnavailableError

PLAN_PRICE_FIELDS = {
    "starter": "stripe_price_starter",
    "growth": "stripe_price_growth",
    "scale": "stripe_price_scale",
}


def _configure() -> None:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise ServiceUnavailableError("Billing is not configured. Set STRIPE_SECRET_KEY.")
    stripe.api_key = settings.stripe_secret_key


def price_id_for_plan(plan_code: str) -> str:
    settings = get_settings()
    field = PLAN_PRICE_FIELDS.get(plan_code)
    if field is None:
        raise ServiceUnavailableError(f"Unknown plan '{plan_code}'.")
    price_id = getattr(settings, field)
    if not price_id:
        raise ServiceUnavailableError(f"Plan '{plan_code}' has no configured Stripe price.")
    return price_id


def create_checkout_session(
    *,
    tenant_id: str,
    customer_email: str,
    plan_code: str,
    success_url: str,
    cancel_url: str,
) -> dict[str, str]:
    _configure()
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=customer_email,
        line_items=[{"price": price_id_for_plan(plan_code), "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=tenant_id,
        metadata={"tenant_id": tenant_id, "plan_code": plan_code},
        subscription_data={"metadata": {"tenant_id": tenant_id, "plan_code": plan_code}},
    )
    return {"checkout_url": session.url or "", "session_id": session.id}


def create_portal_session(*, customer_id: str, return_url: str) -> str:
    _configure()
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )
    return session.url or ""


def verify_webhook(payload: bytes, signature: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise ServiceUnavailableError("Stripe webhook signing is not configured.")
    try:
        event = stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except ValueError as exc:
        raise ServiceUnavailableError("Invalid Stripe webhook payload.") from exc
    except stripe.SignatureVerificationError as exc:
        raise ServiceUnavailableError("Invalid Stripe webhook signature.") from exc
    return dict(event)
