from __future__ import annotations

from reyvo.services.outreach_service import render_template, strip_html


def test_render_template_replaces_known_fields():
    rendered = render_template(
        "Hi {{first_name}}, is {{company}} hiring?",
        {
            "first_name": "Dana",
            "company": "Northside Health",
        },
    )

    assert rendered == "Hi Dana, is Northside Health hiring?"


def test_render_template_tolerates_whitespace_in_placeholders():
    assert render_template("Hi {{ first_name }}!", {"first_name": "Dana"}) == "Hi Dana!"


def test_render_template_drops_unknown_fields():
    assert render_template("Hi {{first_name}} {{unknown}}", {"first_name": "Dana"}) == "Hi Dana "


def test_render_template_does_not_escape_by_default():
    rendered = render_template("Hi {{first_name}}", {"first_name": "Dana & Lee"})

    assert rendered == "Hi Dana & Lee"


def test_render_template_can_escape_html_values():
    rendered = render_template(
        "<p>Hi {{first_name}}</p>",
        {"first_name": "<script>alert('x')</script>"},
        escape_html=True,
    )

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_render_template_only_matches_identifier_like_names():
    # Placeholders cannot smuggle arbitrary attribute access or expressions.
    assert render_template("{{ first-name }}", {"first-name": "Dana"}) == "{{ first-name }}"


def test_strip_html_removes_tags_and_unescapes_entities():
    assert strip_html("<p>Hello &amp; welcome</p>") == "Hello & welcome"


def test_strip_html_trims_surrounding_whitespace():
    assert strip_html("  <p>Hi there</p>\n") == "Hi there"


def test_strip_html_passes_plain_text_through():
    assert strip_html("Just text") == "Just text"
