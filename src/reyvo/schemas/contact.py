from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from reyvo.db.enums import ContactStatus, EmailStatus
from reyvo.schemas.common import ORMModel


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    domain: str | None = Field(default=None, max_length=255)
    industry: str | None = None
    employee_count: int | None = Field(default=None, ge=0)
    city: str | None = None
    state: str | None = None
    country: str | None = None
    website: str | None = None
    linkedin_url: str | None = None
    source: str | None = None
    notes: str | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    domain: str | None = None
    industry: str | None = None
    employee_count: int | None = Field(default=None, ge=0)
    city: str | None = None
    state: str | None = None
    country: str | None = None
    website: str | None = None
    linkedin_url: str | None = None
    source: str | None = None
    notes: str | None = None
    custom_fields: dict[str, Any] | None = None


class CompanyRead(ORMModel):
    id: uuid.UUID
    name: str
    domain: str | None
    industry: str | None
    employee_count: int | None
    city: str | None
    state: str | None
    country: str | None
    website: str | None
    linkedin_url: str | None
    source: str | None
    status: str
    notes: str | None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ContactCreate(BaseModel):
    email: EmailStr
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    full_name: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    company_id: uuid.UUID | None = None
    company_name: str | None = Field(default=None, max_length=255)
    company_domain: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    linkedin_url: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default=None, max_length=120)
    notes: str | None = None
    email_status: EmailStatus = EmailStatus.UNKNOWN
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class ContactUpdate(BaseModel):
    email: EmailStr | None = None
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    full_name: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    company_id: uuid.UUID | None = None
    phone: str | None = Field(default=None, max_length=64)
    linkedin_url: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default=None, max_length=120)
    status: ContactStatus | None = None
    notes: str | None = None
    custom_fields: dict[str, Any] | None = None


class ContactRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    first_name: str | None
    last_name: str | None
    full_name: str
    title: str | None
    email: EmailStr
    email_normalized: EmailStr
    email_status: EmailStatus
    phone: str | None
    linkedin_url: str | None
    source: str | None
    status: ContactStatus
    notes: str | None
    custom_fields: dict[str, Any]
    last_contacted_at: datetime | None
    last_replied_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ContactListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    contact_ids: list[uuid.UUID] = Field(default_factory=list)


class ContactListRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime


class ContactListSummary(ContactListRead):
    contact_count: int = 0


class ContactListMembersAdd(BaseModel):
    contact_ids: list[uuid.UUID] = Field(min_length=1)


class ImportErrorRow(BaseModel):
    row: int
    email: str | None = None
    reason: str


class ImportReport(BaseModel):
    import_id: uuid.UUID
    status: str
    row_count: int
    imported_count: int
    duplicate_count: int
    suppressed_count: int
    error_count: int
    errors: list[ImportErrorRow] = Field(default_factory=list)
