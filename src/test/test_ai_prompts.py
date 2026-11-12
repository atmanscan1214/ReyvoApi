from __future__ import annotations

import json

from reyvo.integrations.ai.base import DraftRequest, ProspectProfile
from reyvo.integrations.ai.prompts import PROMPT_VERSION, build_prompt_payload


def test_prompt_payload_contains_profile_facts():
    profile = ProspectProfile(
        full_name="Dana Reyes",
        current_title="ICU Director",
        company="Northside Health",
        skills=["ICU", "Critical Care"],
        highlights={"certifications": "CCRN"},
    )
    request = DraftRequest(
        agency_name="Acme Staffing",
        contact_name="Dana",
        company="Northside Health",
        tone="consultative",
        value_proposition=None,
        profile=profile,
    )

    profile_json, context_json = build_prompt_payload(request)
    payload = json.loads(profile_json)
    context = json.loads(context_json)

    assert payload["current_title"] == "ICU Director"
    assert payload["skills"] == ["ICU", "Critical Care"]
    assert payload["highlights"] == {"certifications": "CCRN"}
    assert context["agency_name"] == "Acme Staffing"
    assert context["tone"] == "consultative"
    assert "value_proposition" not in context


def test_prompt_payload_drops_empty_values():
    profile = ProspectProfile(
        full_name="Dana Reyes",
        first_name=None,
        current_title="",
        skills=[],
        highlights={},
    )

    profile_json, context_json = build_prompt_payload(
        DraftRequest(
            agency_name="Acme Staffing",
            contact_name="Dana",
            extra_instructions="   ",
            profile=profile,
        )
    )

    assert json.loads(profile_json) == {"full_name": "Dana Reyes"}
    context = json.loads(context_json)
    assert "extra_instructions" not in context
    assert "call_to_action" not in context


def test_prompt_payload_falls_back_to_request_fields_without_profile():
    profile_json, _ = build_prompt_payload(
        DraftRequest(
            agency_name="Acme Staffing",
            contact_name="Sam",
            contact_title="Backend Engineer",
            company="Northside Health",
            industry="Healthcare",
        )
    )

    payload = json.loads(profile_json)
    assert payload["full_name"] == "Sam"
    assert payload["current_title"] == "Backend Engineer"
    assert payload["company"] == "Northside Health"
    assert payload["industry"] == "Healthcare"


def test_profile_to_prompt_dict_omits_empty_values():
    profile = ProspectProfile(
        full_name="Dana Reyes",
        first_name="Dana",
        current_title=None,
        skills=[],
        highlights={},
    )

    assert profile.to_prompt_dict() == {"full_name": "Dana Reyes", "first_name": "Dana"}


def test_prompt_version_is_stable_string():
    assert PROMPT_VERSION == "outreach-personalization-v1"
