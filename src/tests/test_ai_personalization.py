from __future__ import annotations

import uuid

import pytest
from reyvo.db.models import CampaignEnrollment
from reyvo.integrations.ai.base import DraftRequest, DraftResult
from reyvo.integrations.ai.factory import reset_draft_provider, set_draft_provider
from reyvo.integrations.ai.prompts import PROMPT_VERSION
from reyvo.services.outreach_service import (
    build_prospect_profile,
    compose_enrollment_message,
)
from sqlalchemy import select

CANDIDATE_SUBJECT = "Dana, your ICU leadership caught my eye"


class FakeDraftProvider:
    """Captures the request and returns a deterministic personalized draft."""

    def __init__(self, subject: str = CANDIDATE_SUBJECT) -> None:
        self.requests: list[DraftRequest] = []
        self._subject = subject

    async def draft(self, request: DraftRequest) -> DraftResult:
        self.requests.append(request)
        return DraftResult(
            subject=self._subject,
            body_html="<p>Hi Dana, your ICU work stood out.</p>",
            body_text="Hi Dana, your ICU work stood out.",
            model="fake-model",
            prompt_version=PROMPT_VERSION,
        )


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    reset_draft_provider()


async def _create_candidate(client, headers):
    company = await client.post(
        "/api/v1/companies",
        headers=headers,
        json={
            "name": "Northside Health",
            "industry": "Healthcare",
            "city": "Austin",
            "state": "TX",
        },
    )
    assert company.status_code == 201, company.text

    contact = await client.post(
        "/api/v1/contacts",
        headers=headers,
        json={
            "email": "dana.reyes@example.com",
            "first_name": "Dana",
            "last_name": "Reyes",
            "title": "ICU Director",
            "company_id": company.json()["id"],
            "notes": "Leads critical care teams and hires travel nurses for night shifts.",
            "custom_fields": {
                "skills": ["ICU", "Critical Care", "Team leadership"],
                "certifications": "CCRN",
                "availability": "2 weeks",
            },
        },
    )
    assert contact.status_code == 201, contact.text
    return contact.json()


async def _create_campaign(client, headers, contact_id, *, ai_generated: bool):
    response = await client.post(
        "/api/v1/campaigns",
        headers=headers,
        json={
            "name": "Critical care leaders",
            "audience_type": "contacts",
            "contact_ids": [contact_id],
            "steps": [
                {
                    "step_order": 1,
                    "delay_business_days": 0,
                    "subject_template": "Quick question for {{first_name}}",
                    "body_html_template": "<p>Hi {{first_name}}, can we talk?</p>",
                    "body_text_template": "Hi {{first_name}}, can we talk?",
                    "ai_generated": ai_generated,
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_profile_builder_collects_contact_and_company(
    app, client, register_account, auth_headers
):
    tokens = await register_account()
    headers = auth_headers(tokens)
    await _create_candidate(client, headers)

    async with app.state.session_factory() as session:
        from reyvo.db.models import Contact

        # Load the ORM instance through the session so relationships resolve.
        contact = await session.scalar(
            select(Contact).where(Contact.email == "dana.reyes@example.com")
        )
        assert contact is not None
        profile = await build_prospect_profile(session, contact, tenant_name="Acme Staffing")

    assert profile.current_title == "ICU Director"
    assert profile.company == "Northside Health"
    assert profile.industry == "Healthcare"
    assert profile.location == "Austin, TX"
    assert profile.skills == ["ICU", "Critical Care", "Team leadership"]
    assert profile.summary and "critical care" in profile.summary
    assert profile.highlights["certifications"] == "CCRN"
    assert profile.highlights["availability"] == "2 weeks"


async def test_ai_step_preview_is_personalized_from_profile(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)
    contact = await _create_candidate(client, headers)
    campaign = await _create_campaign(client, headers, contact["id"], ai_generated=True)

    provider = FakeDraftProvider()
    set_draft_provider(provider)

    preview = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/preview",
        headers=headers,
        json={"contact_id": contact["id"], "step_order": 1},
    )

    assert preview.status_code == 200, preview.text
    assert preview.json()["subject"] == CANDIDATE_SUBJECT
    assert len(provider.requests) == 1

    request = provider.requests[0]
    assert request.profile is not None
    assert request.profile.current_title == "ICU Director"
    assert request.profile.company == "Northside Health"
    assert request.profile.industry == "Healthcare"
    assert "ICU" in request.profile.skills
    assert request.contact_name == "Dana"
    assert request.agency_name == "Acme Staffing"


async def test_template_step_preview_stays_static(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)
    contact = await _create_candidate(client, headers)
    campaign = await _create_campaign(client, headers, contact["id"], ai_generated=False)

    provider = FakeDraftProvider()
    set_draft_provider(provider)

    preview = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/preview",
        headers=headers,
        json={"contact_id": contact["id"], "step_order": 1},
    )

    assert preview.status_code == 200, preview.text
    assert preview.json()["subject"] == "Quick question for Dana"
    assert provider.requests == []


async def test_ai_step_preview_requires_api_key(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)
    contact = await _create_candidate(client, headers)
    campaign = await _create_campaign(client, headers, contact["id"], ai_generated=True)

    preview = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/preview",
        headers=headers,
        json={"contact_id": contact["id"], "step_order": 1},
    )

    assert preview.status_code == 503
    assert preview.json()["error"]["code"] == "service_unavailable"


async def test_enrollment_message_builder_uses_profile(app, client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)
    contact = await _create_candidate(client, headers)
    campaign = await _create_campaign(client, headers, contact["id"], ai_generated=True)

    for action in ("submit", "approve", "launch"):
        response = await client.post(
            f"/api/v1/campaigns/{campaign['id']}/{action}", headers=headers
        )
        assert response.status_code == 200, response.text

    provider = FakeDraftProvider()
    set_draft_provider(provider)

    async with app.state.session_factory() as session:
        enrollment = await session.scalar(
            select(CampaignEnrollment).where(
                CampaignEnrollment.campaign_id == uuid.UUID(campaign["id"])
            )
        )
        assert enrollment is not None
        message = await compose_enrollment_message(session, enrollment)

    assert message.subject == CANDIDATE_SUBJECT
    assert message.ai_generated is True
    assert message.model == "fake-model"
    assert message.prompt_version == PROMPT_VERSION
    assert message.ai_metadata()["ai_generated"] is True
    assert provider.requests[0].step_order == 1


async def test_draft_endpoint_merges_ad_hoc_profile(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)

    provider = FakeDraftProvider()
    set_draft_provider(provider)

    response = await client.post(
        "/api/v1/ai/draft",
        headers=headers,
        json={
            "contact_name": "Sam",
            "profile": {"title": "Backend Engineer", "skills": "Python, FastAPI"},
            "previous_subjects": ["Old subject"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["prompt_version"] == PROMPT_VERSION
    request = provider.requests[0]
    assert request.profile is not None
    assert request.profile.current_title == "Backend Engineer"
    assert request.profile.skills == ["Python", "FastAPI"]
    assert request.previous_subjects == ["Old subject"]
