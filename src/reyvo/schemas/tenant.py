from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from reyvo.db.enums import MemberRole, TenantStatus
from reyvo.schemas.common import ORMModel


class TenantRead(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    status: TenantStatus
    plan_code: str
    timezone: str
    physical_address: dict[str, Any]
    settings: dict[str, Any]
    trial_ends_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    timezone: str | None = Field(default=None, max_length=64)
    physical_address: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class MemberRead(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    email: EmailStr
    full_name: str
    role: MemberRole
    status: str
    created_at: datetime


class MemberUpdate(BaseModel):
    role: MemberRole


class UsageRead(BaseModel):
    contacts: int
    companies: int
    campaigns: int
    active_campaigns: int
    messages_sent: int
    replies: int
    seats: int
    daily_send_cap: int


class AuditLogRead(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    before_data: dict[str, Any] | None
    after_data: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    request_id: str | None
    created_at: datetime
