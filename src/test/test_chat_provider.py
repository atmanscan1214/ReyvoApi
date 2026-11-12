from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI
from reyvo.core.exceptions import ServiceUnavailableError
from reyvo.integrations.ai.prompts import CHAT_PROMPT_VERSION
from reyvo.integrations.ai.semantic_kernel_chat import SemanticKernelChatProvider


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-chat",
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
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        },
    )


async def test_chat_provider_requires_api_key():
    provider = SemanticKernelChatProvider()

    with pytest.raises(ServiceUnavailableError):
        await provider.answer(question="What is Reyvo?", context="[1] Reyvo is a platform.")


async def test_chat_provider_sends_sources_and_returns_answer():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _chat_response("Tenant isolation uses PostgreSQL RLS [1].")

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://example.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = SemanticKernelChatProvider(async_client=client)

    result = await provider.answer(
        question="How is tenant isolation enforced?",
        context="[1] 5. Multi-Tenancy\nTenant isolation uses PostgreSQL row level security.",
        history="User: Tell me about Reyvo.\nAssistant: Reyvo is a staffing platform.",
    )

    assert result.answer == "Tenant isolation uses PostgreSQL RLS [1]."
    assert result.model == "unit-test-model"
    assert result.prompt_version == CHAT_PROMPT_VERSION
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 7

    body = captured["body"]
    assert isinstance(body, dict)
    messages = json.dumps(body["messages"])
    assert "row level security" in messages
    assert "How is tenant isolation enforced?" in messages
    assert "Tell me about Reyvo." in messages


async def test_chat_provider_wraps_provider_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = SemanticKernelChatProvider(async_client=client)

    with pytest.raises(ServiceUnavailableError):
        await provider.answer(question="Why?", context="[1] Because.")
