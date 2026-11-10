from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from reyvo.schemas.common import ORMModel


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    full_name: str = Field(min_length=1, max_length=255)
    agency_name: str = Field(min_length=2, max_length=255)
    timezone: str = Field(default="UTC", max_length=64)
    physical_address: dict[str, Any] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_slug: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class TenantSummary(ORMModel):
    id: uuid.UUID
    name: str
    slug: str


class UserRead(ORMModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    is_platform_admin: bool
    status: str
    email_verified_at: datetime | None = None
    created_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant: TenantSummary


class MeResponse(BaseModel):
    user: UserRead
    tenant: TenantSummary
    role: str
