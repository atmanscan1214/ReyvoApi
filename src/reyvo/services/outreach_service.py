"""Outreach message composition.

Turns a campaign step plus a prospective employee's profile into the exact
subject/body that gets stored on a ``Message``. Steps flagged ``ai_generated``
are personalized by the configured draft provider (Semantic Kernel by default);
everything else is rendered from the step's merge-field templates.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from reyvo.core.exceptions import NotFoundError
from reyvo.db.enums import MessageDirection
from reyvo.db.models import (
    Campaign,
    CampaignEnrollment,
    CampaignStep,
    Company,
    Contact,
    EmailAccount,
    Message,
    Tenant,
)
from reyvo.integrations.ai.base import DraftRequest, ProspectProfile
from reyvo.integrations.ai.factory import get_draft_provider
from reyvo.integrations.ai.prompts import PROMPT_VERSION

_MERGE_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
_TAG_RE = re.compile(r"<[^>]+>")
_SKILL_KEYS = ("skills", "skill_set", "skill set", "technologies", "tools", "expertise")
_SUMMARY_KEYS = ("summary", "bio", "about", "headline", "profile_summary", "experience_summary")
_LOCATION_KEYS = ("location", "city", "region", "address")
_MAX_SUMMARY_CHARS = 1_200
_MAX_HIGHLIGHT_CHARS = 300
_MAX_HIGHLIGHTS = 12
_MAX_SKILLS = 20
_MAX_SKILL_CHARS = 60


@dataclass(slots=True)
class OutreachMessage:
    subject: str
    body_html: str
    body_text: str
    ai_generated: bool = False
    model: str | None = None
    prompt_version: str | None = None

    def ai_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {"ai_generated": self.ai_generated}
        if self.model:
            metadata["model"] = self.model
        if self.prompt_version:
            metadata["prompt_version"] = self.prompt_version
        return metadata


def render_template(template: str, context: dict[str, Any], *, escape_html: bool = False) -> str:
    def replace(match: re.Match[str]) -> str:
        value = context.get(match.group(1), "")
        text = "" if value is None else str(value)
        return html.escape(text) if escape_html else text

    return _MERGE_RE.sub(replace, template)


def strip_html(value: str) -> str:
    return html.unescape(_TAG_RE.sub("", value)).strip()


def _truncate(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple | set):
        return ", ".join(item for item in (_stringify(entry) for entry in value) if item)
    if isinstance(value, dict):
        return ", ".join(
            f"{key}: {text}" for key, item in value.items() if (text := _stringify(item))
        )
    return str(value).strip()


def _first_present(fields: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in fields and fields[key] not in (None, "", [], {}):
            return fields[key]
    return None


def _extract_skills(fields: dict[str, Any]) -> list[str]:
    raw = _first_present(fields, _SKILL_KEYS)
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = re.split(r"[,;\n|/]", raw)
    elif isinstance(raw, list | tuple | set):
        parts = list(raw)
    else:
        parts = [raw]

    skills: list[str] = []
    for part in parts:
        text = _truncate(_stringify(part), _MAX_SKILL_CHARS)
        if text and text.lower() not in {skill.lower() for skill in skills}:
            skills.append(text)
        if len(skills) >= _MAX_SKILLS:
            break
    return skills


async def _load_company(session: AsyncSession, contact: Contact) -> Company | None:
    if contact.company_id is None:
        return None
    return await session.scalar(
        select(Company).where(
            Company.id == contact.company_id,
            Company.tenant_id == contact.tenant_id,
        )
    )


async def build_prospect_profile(
    session: AsyncSession, contact: Contact, *, tenant_name: str | None = None
) -> ProspectProfile:
    """Assemble the candidate facts the AI is allowed to use."""

    company = await _load_company(session, contact)
    fields = dict(contact.custom_fields or {})

    location = ""
    if company is not None:
        location = ", ".join(
            part for part in (company.city, company.state, company.country) if part
        )
    if not location:
        location = _stringify(_first_present(fields, _LOCATION_KEYS))

    summary_raw = contact.notes or _stringify(_first_present(fields, _SUMMARY_KEYS))
    summary = _truncate(summary_raw, _MAX_SUMMARY_CHARS) if summary_raw else None

    skills = _extract_skills(fields)

    used_keys = {key.lower() for key in (*_SKILL_KEYS, *_SUMMARY_KEYS, *_LOCATION_KEYS)}
    highlights: dict[str, str] = {}
    for key, value in fields.items():
        if key.lower() in used_keys or key.lower() == "industry":
            continue
        text = _truncate(_stringify(value), _MAX_HIGHLIGHT_CHARS)
        if not text:
            continue
        highlights[str(key)] = text
        if len(highlights) >= _MAX_HIGHLIGHTS:
            break

    industry = (company.industry if company is not None else None) or _stringify(
        fields.get("industry")
    )

    return ProspectProfile(
        full_name=contact.full_name,
        first_name=contact.first_name,
        current_title=contact.title,
        company=company.name if company is not None else None,
        industry=industry or None,
        location=location or None,
        linkedin_url=contact.linkedin_url,
        summary=summary,
        skills=skills,
        highlights=highlights,
    )


async def build_merge_context(
    session: AsyncSession, contact: Contact, *, tenant_name: str
) -> dict[str, Any]:
    company = await _load_company(session, contact)
    context: dict[str, Any] = {
        "first_name": contact.first_name or "",
        "last_name": contact.last_name or "",
        "full_name": contact.full_name,
        "title": contact.title or "",
        "email": contact.email_normalized,
        "company": company.name if company is not None else "",
        "company_domain": (company.domain if company is not None else "") or "",
        "agency_name": tenant_name,
    }
    context.update(contact.custom_fields or {})
    return context


def _campaign_setting(campaign: Campaign, key: str) -> str | None:
    settings = campaign.settings or {}
    ai_settings = settings.get("ai")
    if isinstance(ai_settings, dict) and ai_settings.get(key):
        return str(ai_settings[key])
    value = settings.get(key)
    return str(value) if value not in (None, "") else None


def build_draft_request(
    *,
    tenant: Tenant,
    campaign: Campaign,
    step: CampaignStep,
    contact: Contact,
    profile: ProspectProfile,
    previous_subjects: list[str] | None = None,
    sender_name: str | None = None,
) -> DraftRequest:
    return DraftRequest(
        agency_name=tenant.name,
        contact_name=contact.first_name or contact.full_name,
        contact_title=contact.title,
        company=profile.company,
        industry=profile.industry,
        step_order=step.step_order,
        tone=_campaign_setting(campaign, "tone") or "consultative",
        value_proposition=_campaign_setting(campaign, "value_proposition"),
        call_to_action=_campaign_setting(campaign, "call_to_action"),
        extra_instructions=_campaign_setting(campaign, "extra_instructions"),
        previous_subjects=list(previous_subjects or []),
        sender_name=sender_name,
        profile=profile,
    )


async def compose_outreach_message(
    session: AsyncSession,
    *,
    tenant: Tenant,
    campaign: Campaign,
    step: CampaignStep,
    contact: Contact,
    previous_subjects: list[str] | None = None,
    sender_name: str | None = None,
) -> OutreachMessage:
    """Render the email for one enrollment, personalizing it with AI when enabled."""

    if step.ai_generated:
        profile = await build_prospect_profile(session, contact, tenant_name=tenant.name)
        request = build_draft_request(
            tenant=tenant,
            campaign=campaign,
            step=step,
            contact=contact,
            profile=profile,
            previous_subjects=previous_subjects,
            sender_name=sender_name,
        )
        result = await get_draft_provider().draft(request)
        return OutreachMessage(
            subject=result.subject,
            body_html=result.body_html,
            body_text=result.body_text,
            ai_generated=True,
            model=result.model,
            prompt_version=result.prompt_version or PROMPT_VERSION,
        )

    context = await build_merge_context(session, contact, tenant_name=tenant.name)
    body_text_template = step.body_text_template or strip_html(step.body_html_template)
    return OutreachMessage(
        subject=render_template(step.subject_template, context),
        body_html=render_template(step.body_html_template, context, escape_html=True),
        body_text=render_template(body_text_template, context),
    )


async def compose_enrollment_message(
    session: AsyncSession,
    enrollment: CampaignEnrollment,
    *,
    sender_name: str | None = None,
) -> OutreachMessage:
    """Render the next email for an enrollment, ready to be sent and persisted.

    The sender worker calls this for each due enrollment so every prospective
    employee receives content generated from their own profile rather than a
    shared template. Prior subjects are passed along so follow-ups do not repeat
    the same angle.
    """

    campaign = await session.scalar(
        select(Campaign)
        .options(selectinload(Campaign.steps))
        .where(
            Campaign.id == enrollment.campaign_id,
            Campaign.tenant_id == enrollment.tenant_id,
        )
    )
    if campaign is None:
        raise NotFoundError("The enrollment's campaign no longer exists.")

    contact = await session.scalar(
        select(Contact).where(
            Contact.id == enrollment.contact_id,
            Contact.tenant_id == enrollment.tenant_id,
        )
    )
    if contact is None:
        raise NotFoundError("The enrollment's contact no longer exists.")

    step = next(
        (item for item in campaign.steps if item.step_order == enrollment.current_step_order),
        None,
    )
    if step is None:
        raise NotFoundError("The enrollment has no step left to send.")

    tenant = await session.get(Tenant, enrollment.tenant_id)
    if tenant is None:
        raise NotFoundError("The enrollment's workspace no longer exists.")

    previous_subjects = list(
        (
            await session.scalars(
                select(Message.subject)
                .where(
                    Message.enrollment_id == enrollment.id,
                    Message.direction == MessageDirection.OUTBOUND,
                )
                .order_by(Message.sent_at, Message.created_at)
            )
        ).all()
    )

    if sender_name is None and enrollment.email_account_id is not None:
        account = await session.get(EmailAccount, enrollment.email_account_id)
        if account is not None:
            sender_name = account.from_name or account.display_name

    return await compose_outreach_message(
        session,
        tenant=tenant,
        campaign=campaign,
        step=step,
        contact=contact,
        previous_subjects=previous_subjects,
        sender_name=sender_name,
    )
