from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from reyvo.db.enums import ApprovalStatus, CampaignStatus, EnrollmentStatus
from reyvo.schemas.common import ORMModel


class CampaignStepCreate(BaseModel):
    step_order: int = Field(ge=1, le=8)
    delay_business_days: int = Field(default=0, ge=0, le=30)
    subject_template: str = Field(min_length=1, max_length=500)
    body_html_template: str = Field(min_length=1)
    body_text_template: str | None = None
    ai_generated: bool = False
    ai_prompt_version: str | None = None


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    audience_type: Literal["contacts", "list", "segment"] = "contacts"
    audience_id: uuid.UUID | None = None
    contact_ids: list[uuid.UUID] = Field(default_factory=list)
    email_account_ids: list[uuid.UUID] = Field(default_factory=list)
    timezone: str = Field(default="UTC", max_length=64)
    sending_window: dict[str, Any] = Field(default_factory=dict)
    daily_cap: int = Field(default=200, ge=1, le=2_000)
    stop_on_reply: bool = True
    steps: list[CampaignStepCreate] = Field(min_length=1, max_length=8)


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    timezone: str | None = Field(default=None, max_length=64)
    sending_window: dict[str, Any] | None = None
    daily_cap: int | None = Field(default=None, ge=1, le=2_000)
    stop_on_reply: bool | None = None
    email_account_ids: list[uuid.UUID] | None = None
    steps: list[CampaignStepCreate] | None = Field(default=None, min_length=1, max_length=8)


class CampaignStepRead(ORMModel):
    id: uuid.UUID
    step_order: int
    delay_business_days: int
    subject_template: str
    body_html_template: str
    body_text_template: str | None
    ai_generated: bool
    approval_status: ApprovalStatus


class CampaignRead(ORMModel):
    id: uuid.UUID
    name: str
    status: CampaignStatus
    audience_type: str
    audience_id: uuid.UUID | None
    audience_snapshot: list[str]
    email_account_ids: list[str]
    timezone: str
    sending_window: dict[str, Any]
    daily_cap: int
    stop_on_reply: bool
    approval_status: ApprovalStatus
    approved_at: datetime | None
    launched_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    steps: list[CampaignStepRead] = Field(default_factory=list)


class CampaignPreviewRequest(BaseModel):
    contact_id: uuid.UUID
    step_order: int = Field(default=1, ge=1, le=8)


class CampaignPreviewResponse(BaseModel):
    subject: str
    body_html: str
    body_text: str


class EnrollmentRead(ORMModel):
    id: uuid.UUID
    contact_id: uuid.UUID
    email_account_id: uuid.UUID | None
    status: EnrollmentStatus
    current_step_order: int
    next_send_at: datetime | None
    last_message_at: datetime | None
    stopped_reason: str | None


class CampaignStats(BaseModel):
    enrolled: int
    active: int
    stopped: int
    completed: int
    sent: int
    replies: int


class CampaignPreflight(BaseModel):
    ok: bool
    errors: list[str]
    warnings: list[str]
    audience_size: int
    sendable: int
    sending_accounts: int


class CampaignRejectRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)
