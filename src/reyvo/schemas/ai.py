from __future__ import annotations

import uuid
from typing import Any

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
    previous_subjects: list[str] = Field(default_factory=list, max_length=8)
    sender_name: str | None = Field(default=None, max_length=255)
    profile: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional ad-hoc profile facts merged over the stored contact profile "
            "(title, company, industry, location, summary, skills, ...)."
        ),
    )


class DraftStepResponse(BaseModel):
    subject: str
    body_html: str
    body_text: str
    model: str
    prompt_version: str = ""
