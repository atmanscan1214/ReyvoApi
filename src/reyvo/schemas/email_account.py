from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from reyvo.db.enums import EmailAccountStatus, EmailProvider
from reyvo.schemas.common import ORMModel


class EmailAccountRead(ORMModel):
    id: uuid.UUID
    provider: EmailProvider
    email: str
    display_name: str | None
    from_name: str | None
    reply_to: str | None
    status: EmailAccountStatus
    daily_cap: int
    hourly_cap: int
    ramp_stage: int
    warmup_started_at: datetime | None
    last_health_check_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class EmailAccountUpdate(BaseModel):
    from_name: str | None = Field(default=None, max_length=255)
    reply_to: str | None = Field(default=None, max_length=320)
    signature_html: str | None = None
    daily_cap: int | None = Field(default=None, ge=1, le=2_000)
    hourly_cap: int | None = Field(default=None, ge=1, le=500)


class OAuthStartResponse(BaseModel):
    authorization_url: str
    state: str


class OAuthCallbackResponse(BaseModel):
    account: EmailAccountRead
    message: str


class EmailAccountTestResponse(BaseModel):
    ok: bool
    message: str
