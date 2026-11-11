from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class DraftRequest:
    agency_name: str
    contact_name: str
    contact_title: str | None = None
    company: str | None = None
    industry: str | None = None
    step_order: int = 1
    tone: str = "consultative"
    value_proposition: str | None = None
    call_to_action: str | None = None
    extra_instructions: str | None = None
    previous_subjects: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DraftResult:
    subject: str
    body_html: str
    body_text: str
    model: str


class DraftProvider(Protocol):
    async def draft(self, request: DraftRequest) -> DraftResult: ...
