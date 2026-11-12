from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ProspectProfile:
    """Everything we know about the prospective employee being contacted.

    Only factual details that exist on the contact/company record belong here;
    the prompt explicitly forbids the model from inventing anything else.
    """

    full_name: str
    first_name: str | None = None
    current_title: str | None = None
    company: str | None = None
    industry: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    summary: str | None = None
    skills: list[str] = field(default_factory=list)
    highlights: dict[str, str] = field(default_factory=dict)

    def to_prompt_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "full_name": self.full_name,
            "first_name": self.first_name,
            "current_title": self.current_title,
            "company": self.company,
            "industry": self.industry,
            "location": self.location,
            "linkedin_url": self.linkedin_url,
            "summary": self.summary,
            "skills": self.skills,
            "highlights": self.highlights,
        }
        return {key: value for key, value in data.items() if value not in (None, "", [], {})}


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
    sender_name: str | None = None
    profile: ProspectProfile | None = None


@dataclass(slots=True)
class DraftResult:
    subject: str
    body_html: str
    body_text: str
    model: str
    prompt_version: str = ""


@dataclass(slots=True)
class ChatResult:
    answer: str
    model: str
    prompt_version: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class DraftProvider(Protocol):
    async def draft(self, request: DraftRequest) -> DraftResult: ...


class ChatProvider(Protocol):
    async def answer(self, *, question: str, context: str, history: str) -> ChatResult: ...
