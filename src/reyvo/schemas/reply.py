from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from reyvo.db.enums import ReplyCategory, TaskStatus
from reyvo.schemas.common import ORMModel


class ReplyRead(ORMModel):
    id: uuid.UUID
    message_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    campaign_id: uuid.UUID | None
    category: ReplyCategory
    confidence: float | None
    classified_by: str
    needs_review: bool
    snippet: str | None
    received_at: datetime | None
    assigned_to: uuid.UUID | None
    created_at: datetime


class ReplyUpdate(BaseModel):
    category: ReplyCategory | None = None
    needs_review: bool | None = None
    assigned_to: uuid.UUID | None = None


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    due_at: datetime | None = None
    priority: str = Field(default="normal", max_length=20)
    contact_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    due_at: datetime | None = None
    priority: str | None = Field(default=None, max_length=20)
    status: TaskStatus | None = None
    assignee_id: uuid.UUID | None = None


class TaskRead(ORMModel):
    id: uuid.UUID
    contact_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None
    assignee_id: uuid.UUID | None
    title: str
    due_at: datetime | None
    status: TaskStatus
    priority: str
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
