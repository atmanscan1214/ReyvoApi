from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.exceptions import BadRequestError
from reyvo.core.tenancy import Principal
from reyvo.db.enums import ReplyCategory
from reyvo.db.models import Contact
from reyvo.integrations.ai.base import DraftRequest, DraftResult, ProspectProfile
from reyvo.integrations.ai.factory import get_draft_provider
from reyvo.schemas.ai import DraftStepRequest
from reyvo.services.outreach_service import build_prospect_profile

_PROFILE_FIELDS = {
    "full_name": "full_name",
    "first_name": "first_name",
    "title": "current_title",
    "current_title": "current_title",
    "company": "company",
    "industry": "industry",
    "location": "location",
    "linkedin_url": "linkedin_url",
    "summary": "summary",
}

_UNSUBSCRIBE = re.compile(
    r"\b(unsubscribe|remove me|take me off|stop emailing|opt out|do not contact)\b",
    re.IGNORECASE,
)
_OUT_OF_OFFICE = re.compile(
    r"\b(out of (the )?office|automatic reply|auto-?reply|on vacation|on leave|maternity leave)\b",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"\b(not interested|no thanks|no thank you|not a fit|stop reaching out|decline|pass)\b",
    re.IGNORECASE,
)
_POSITIVE = re.compile(
    r"\b(interested|let'?s (chat|talk)|happy to|sounds good|book|schedule|call me|meet)\b",
    re.IGNORECASE,
)
_QUESTION = re.compile(r"\?")


async def draft_campaign_step(
    session: AsyncSession, principal: Principal, payload: DraftStepRequest
) -> DraftResult:
    contact_name = payload.contact_name or "there"
    contact_title = payload.contact_title
    company = payload.company
    profile: ProspectProfile | None = None

    if payload.contact_id is not None:
        contact = await session.scalar(
            select(Contact).where(
                Contact.id == payload.contact_id,
                Contact.tenant_id == principal.tenant_id,
            )
        )
        if contact is None:
            raise BadRequestError("The selected contact does not exist in this workspace.")
        contact_name = contact.first_name or contact.full_name
        contact_title = contact.title or contact_title
        profile = await build_prospect_profile(session, contact, tenant_name=principal.tenant.name)

    if payload.profile:
        if profile is None:
            profile = ProspectProfile(full_name=contact_name)
        profile = _apply_profile_overrides(profile, payload.profile)

    provider = get_draft_provider()
    return await provider.draft(
        DraftRequest(
            agency_name=principal.tenant.name,
            contact_name=contact_name,
            contact_title=contact_title,
            company=company,
            industry=payload.industry,
            step_order=payload.step_order,
            tone=payload.tone,
            value_proposition=payload.value_proposition,
            call_to_action=payload.call_to_action,
            extra_instructions=payload.extra_instructions,
            previous_subjects=list(payload.previous_subjects),
            sender_name=payload.sender_name,
            profile=profile,
        )
    )


def _apply_profile_overrides(
    profile: ProspectProfile, overrides: dict[str, object]
) -> ProspectProfile:
    """Merge ad-hoc profile values supplied by API callers onto a stored profile."""

    for key, value in overrides.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        target = _PROFILE_FIELDS.get(key.strip().lower())
        if target is not None:
            setattr(profile, target, text)
        elif key.strip().lower() in {"skills", "skill_set", "technologies"}:
            profile.skills = [
                part.strip() for part in text.replace(";", ",").split(",") if part.strip()
            ][:20]
        else:
            profile.highlights[str(key)] = text[:300]
    return profile


def classify_reply_text(text: str) -> tuple[ReplyCategory, float, bool]:
    """Rule-based classification used before/without AI.

    Returns the category, a confidence score, and whether a human should review it.
    """

    sample = text.strip()
    if not sample:
        return ReplyCategory.OTHER, 0.2, True
    if _UNSUBSCRIBE.search(sample):
        return ReplyCategory.UNSUBSCRIBE, 0.95, False
    if _OUT_OF_OFFICE.search(sample):
        return ReplyCategory.OUT_OF_OFFICE, 0.9, False
    if _NEGATIVE.search(sample):
        return ReplyCategory.NEGATIVE, 0.85, False
    if _POSITIVE.search(sample):
        return ReplyCategory.POSITIVE, 0.8, False
    if _QUESTION.search(sample):
        return ReplyCategory.QUESTION, 0.6, True
    return ReplyCategory.NEUTRAL, 0.4, True
