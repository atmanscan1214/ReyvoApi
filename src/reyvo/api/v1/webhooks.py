from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.exceptions import BadRequestError
from reyvo.core.utils import utcnow
from reyvo.db.models import WebhookEvent
from reyvo.db.session import get_db
from reyvo.integrations.billing import stripe_gateway
from reyvo.services import billing_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if not stripe_signature:
        raise BadRequestError("Missing Stripe-Signature header.")

    payload = await request.body()
    event = stripe_gateway.verify_webhook(payload, stripe_signature)
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")

    existing = await session.scalar(
        select(WebhookEvent.id).where(
            WebhookEvent.provider == "stripe",
            WebhookEvent.event_id == event_id,
        )
    )
    if existing is not None:
        return {"received": True, "duplicate": True, "type": event_type}

    record = WebhookEvent(
        provider="stripe",
        event_id=event_id,
        payload=event,
        status="processing",
        attempts=1,
    )
    session.add(record)
    await session.flush()

    try:
        await billing_service.handle_webhook_event(session, event)
    except Exception:
        record.status = "failed"
        await session.commit()
        raise

    record.status = "processed"
    record.processed_at = utcnow()
    await session.commit()
    return {"received": True, "duplicate": False, "type": event_type}
