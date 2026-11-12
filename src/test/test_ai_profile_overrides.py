from __future__ import annotations

from reyvo.integrations.ai.base import ProspectProfile
from reyvo.services.ai_service import _apply_profile_overrides


def _profile() -> ProspectProfile:
    return ProspectProfile(
        full_name="Dana Reyes",
        first_name="Dana",
        current_title="ICU Director",
        company="Northside Health",
        highlights={"certifications": "CCRN"},
    )


def test_known_fields_override_existing_values():
    profile = _apply_profile_overrides(
        _profile(),
        {
            "title": "Director of Nursing",
            "company": "Lakeside Medical",
            "industry": "Healthcare",
            "location": "Austin, TX",
        },
    )

    assert profile.current_title == "Director of Nursing"
    assert profile.company == "Lakeside Medical"
    assert profile.industry == "Healthcare"
    assert profile.location == "Austin, TX"


def test_full_name_and_first_name_are_supported_keys():
    profile = _apply_profile_overrides(
        _profile(), {"full_name": "Dana R. Reyes", "first_name": "Dana"}
    )

    assert profile.full_name == "Dana R. Reyes"
    assert profile.first_name == "Dana"


def test_skills_string_is_split_into_a_list():
    profile = _apply_profile_overrides(
        _profile(), {"skills": "ICU, Critical Care; Team leadership"}
    )

    assert profile.skills == ["ICU", "Critical Care", "Team leadership"]


def test_skills_are_trimmed_and_blank_entries_are_dropped():
    profile = _apply_profile_overrides(_profile(), {"technologies": " Python ,, FastAPI "})

    assert profile.skills == ["Python", "FastAPI"]


def test_unknown_fields_become_highlights():
    profile = _apply_profile_overrides(
        _profile(), {"availability": "2 weeks", "certifications": "CCRN-A"}
    )

    assert profile.highlights["availability"] == "2 weeks"
    assert profile.highlights["certifications"] == "CCRN-A"


def test_long_highlight_values_are_truncated():
    profile = _apply_profile_overrides(_profile(), {"note": "x" * 400})

    assert len(profile.highlights["note"]) == 300


def test_none_and_blank_values_are_ignored():
    profile = _apply_profile_overrides(
        _profile(),
        {
            "title": None,
            "company": "   ",
            "availability": "",
        },
    )

    assert profile.current_title == "ICU Director"
    assert profile.company == "Northside Health"
    assert "availability" not in profile.highlights


def test_overrides_mutate_and_return_the_same_profile():
    profile = _profile()

    result = _apply_profile_overrides(profile, {"title": "VP Nursing"})

    assert result is profile
