from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from reyvo.db.enums import SuppressionReason
from reyvo.schemas.common import ORMModel


class SuppressionCreate(BaseModel):
    email: EmailStr
    reason: SuppressionReason = SuppressionReason.MANUAL
    note: str | None = None


class SuppressionRead(ORMModel):
    id: uuid.UUID
    email_normalized: EmailStr
    reason: SuppressionReason
    source: str | None
    note: str | None
    created_at: datetime


class UnsubscribeInfo(BaseModel):
    valid: bool
    tenant_name: str
    masked_email: str


class UnsubscribeResponse(BaseModel):
    message: str
    already_suppressed: bool = False


class UnsubscribeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)
