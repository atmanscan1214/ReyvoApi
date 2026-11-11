from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from reyvo.db.enums import SubscriptionStatus
from reyvo.schemas.common import ORMModel


class PlanRead(ORMModel):
    code: str
    name: str
    stripe_price_id: str | None
    limits: dict
    active: bool


class SubscriptionRead(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    plan_code: str
    status: SubscriptionStatus
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    seats: int
    trial_ends_at: datetime | None


class CheckoutRequest(BaseModel):
    plan_code: str = Field(min_length=1, max_length=40)
    success_url: str | None = None
    cancel_url: str | None = None


class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str


class BillingPortalResponse(BaseModel):
    url: str
