from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from reyvo.db.enums import OpportunityStage
from reyvo.schemas.common import ORMModel


class OpportunityCreate(BaseModel):
    company_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    stage: OpportunityStage = OpportunityStage.NEW
    value_cents: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    owner_id: uuid.UUID | None = None
    source_campaign_id: uuid.UUID | None = None
    expected_close_date: date | None = None
    notes: str | None = None


class OpportunityUpdate(BaseModel):
    stage: OpportunityStage | None = None
    value_cents: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    owner_id: uuid.UUID | None = None
    expected_close_date: date | None = None
    notes: str | None = None


class OpportunityRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    stage: OpportunityStage
    value_cents: int
    currency: str
    owner_id: uuid.UUID | None
    source_campaign_id: uuid.UUID | None
    expected_close_date: date | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class PipelineSummary(BaseModel):
    stage: OpportunityStage
    count: int
    value_cents: int
