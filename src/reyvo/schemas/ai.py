from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class DraftStepRequest(BaseModel):
    contact_id: uuid.UUID | None = None
    contact_name: str | None = Field(default=None, max_length=255)
    contact_title: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=120)
    step_order: int = Field(default=1, ge=1, le=8)
    tone: str = Field(default="consultative", max_length=40)
    value_proposition: str | None = None
    call_to_action: str | None = None
    extra_instructions: str | None = None


class DraftStepResponse(BaseModel):
    subject: str
    body_html: str
    body_text: str
    model: str
