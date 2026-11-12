"""Embedding provider for knowledge-base retrieval.

Embeddings are optional: when ``AI_EMBEDDING_MODEL`` is empty or no API key is
configured, the knowledge base still works with lexical (BM25) search only.
"""

from __future__ import annotations

import logging
from typing import Protocol

from openai import AsyncOpenAI
from semantic_kernel.connectors.ai.open_ai import OpenAITextEmbedding

from reyvo.core.config import get_settings
from reyvo.core.exceptions import ServiceUnavailableError

SERVICE_ID = "reyvo-embeddings"
BATCH_SIZE = 64

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    @property
    def available(self) -> bool: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class SemanticKernelEmbeddingProvider:
    """Embedding generation through the Semantic Kernel OpenAI connector."""

    provider_name = "semantic-kernel"

    def __init__(self, *, async_client: AsyncOpenAI | None = None) -> None:
        settings = get_settings()
        self._api_key = settings.ai_api_key
        self._base_url = settings.ai_base_url.rstrip("/") or "https://api.openai.com/v1"
        self._model = settings.ai_embedding_model.strip()
        self._client = async_client
        self._service: OpenAITextEmbedding | None = None

    @property
    def available(self) -> bool:
        return bool(self._model) and (bool(self._api_key) or self._client is not None)

    def _build_service(self) -> OpenAITextEmbedding:
        client = self._client or AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=60.0,
            max_retries=1,
        )
        return OpenAITextEmbedding(
            ai_model_id=self._model,
            async_client=client,
            service_id=SERVICE_ID,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.available:
            raise ServiceUnavailableError(
                "Embeddings are not configured. "
                "Set AI_API_KEY and AI_EMBEDDING_MODEL to enable them."
            )
        if not texts:
            return []
        if self._service is None:
            self._service = self._build_service()
        try:
            vectors = await self._service.generate_embeddings(texts, batch_size=BATCH_SIZE)
        except ServiceUnavailableError:
            raise
        except Exception as exc:  # pragma: no cover - network/provider failure
            logger.warning("Embedding provider request failed.", exc_info=True)
            raise ServiceUnavailableError(
                "The embedding provider could not process the request right now."
            ) from exc
        return [[float(value) for value in vector] for vector in vectors]
