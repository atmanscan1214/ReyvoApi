from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI
from reyvo.core.exceptions import ServiceUnavailableError
from reyvo.integrations.ai.embeddings import SemanticKernelEmbeddingProvider


async def test_embedding_provider_is_unavailable_without_a_key():
    provider = SemanticKernelEmbeddingProvider()

    assert provider.available is False
    with pytest.raises(ServiceUnavailableError):
        await provider.embed(["hello"])


async def test_embedding_provider_returns_vectors():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["body"] = body
        inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [
                    {
                        "object": "embedding",
                        "index": index,
                        "embedding": [float(index), 0.5, 0.25],
                    }
                    for index in range(len(inputs))
                ],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://example.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = SemanticKernelEmbeddingProvider(async_client=client)

    vectors = await provider.embed(["first source", "second source"])

    assert provider.available is True
    assert vectors == [[0.0, 0.5, 0.25], [1.0, 0.5, 0.25]]
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "text-embedding-3-small"


async def test_embedding_provider_handles_empty_input():
    provider = SemanticKernelEmbeddingProvider(async_client=object())  # type: ignore[arg-type]

    assert await provider.embed([]) == []
