from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI
from reyvo.core.exceptions import ServiceUnavailableError
from reyvo.integrations.ai.base import DraftRequest, ProspectProfile
from reyvo.integrations.ai.prompts import PROMPT_VERSION
from reyvo.integrations.ai.semantic_kernel_provider import (
    SemanticKernelDraftProvider,
    extract_json_object,
    parse_draft_response,
)


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


async def test_provider_requires_api_key():
    provider = SemanticKernelDraftProvider()

    with pytest.raises(ServiceUnavailableError):
        await provider.draft(DraftRequest(agency_name="Acme", contact_name="Dana"))


async def test_semantic_kernel_provider_sends_profile_to_model():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _chat_response(
            json.dumps(
                {
                    "subject": "Your ICU team, Dana",
                    "body_text": "Hi Dana, your ICU leadership stood out.",
                    "body_html": "<p>Hi Dana, your ICU leadership stood out.</p>",
                }
            )
        )

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://example.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = SemanticKernelDraftProvider(async_client=client)

    result = await provider.draft(
        DraftRequest(
            agency_name="Acme Staffing",
            contact_name="Dana",
            profile=ProspectProfile(
                full_name="Dana Reyes",
                current_title="ICU Director",
                company="Northside Health",
                industry="Healthcare",
                skills=["ICU", "Critical Care"],
            ),
        )
    )

    assert result.subject == "Your ICU team, Dana"
    assert result.model == "unit-test-model"
    assert result.prompt_version == PROMPT_VERSION

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"] == {"type": "json_object"}

    prompt_text = json.dumps(body["messages"])
    assert "ICU Director" in prompt_text
    assert "Critical Care" in prompt_text
    assert "Northside Health" in prompt_text


async def test_semantic_kernel_provider_wraps_provider_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = SemanticKernelDraftProvider(async_client=client)

    with pytest.raises(ServiceUnavailableError):
        await provider.draft(DraftRequest(agency_name="Acme", contact_name="Dana"))


def test_parse_draft_response_falls_back_to_html():
    result = parse_draft_response(
        json.dumps(
            {
                "subject": "A quick question",
                "body_text": "Hi Dana,\n\nYour ICU work stood out.",
            }
        ),
        model="test-model",
    )

    assert result.subject == "A quick question"
    assert result.body_html.count("<p>") == 2
    assert result.prompt_version == PROMPT_VERSION


def test_parse_draft_response_escapes_generated_html_fallback():
    result = parse_draft_response(
        json.dumps({"subject": "Hi", "body_text": "Use <b>bold</b> & more"}),
        model="test-model",
    )

    assert "<b>" not in result.body_html
    assert "&lt;b&gt;" in result.body_html


def test_parse_draft_response_rejects_missing_fields():
    with pytest.raises(ServiceUnavailableError):
        parse_draft_response(json.dumps({"subject": "Only a subject"}), model="test-model")


def test_extract_json_object_handles_code_fences_and_noise():
    fenced = '```json\n{"subject": "Hi", "body_text": "Hello"}\n```'
    assert extract_json_object(fenced)["subject"] == "Hi"

    noisy = 'Sure: {"subject": "Hi", "body_text": "Hello"} hope that helps'
    assert extract_json_object(noisy)["body_text"] == "Hello"

    # JSON embedded in a code fence with no newline after the marker.
    inline = '```{"subject": "Hi", "body_text": "Hello"}```'
    assert extract_json_object(inline)["subject"] == "Hi"


def test_extract_json_object_rejects_unreadable_content():
    with pytest.raises(ServiceUnavailableError):
        extract_json_object("no json here")


def test_extract_json_object_rejects_non_object_json():
    with pytest.raises(ServiceUnavailableError):
        extract_json_object('["subject", "body_text"]')
