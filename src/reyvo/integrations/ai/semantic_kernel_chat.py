"""Semantic Kernel chat over the database knowledge base.

The kernel receives retrieved sources as a prompt argument, so document text
never becomes part of the template itself. The answer is plain text with
``[n]`` source markers; citations are resolved server-side from the retrieved
chunks, not trusted from the model.
"""

from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import (
    OpenAIChatCompletion,
    OpenAIChatPromptExecutionSettings,
)
from semantic_kernel.functions import KernelArguments

from reyvo.core.config import get_settings
from reyvo.core.exceptions import ServiceUnavailableError
from reyvo.integrations.ai.base import ChatResult
from reyvo.integrations.ai.prompts import CHAT_PROMPT_TEMPLATE, CHAT_PROMPT_VERSION

SERVICE_ID = "reyvo-knowledge-chat"
PLUGIN_NAME = "knowledge"
FUNCTION_NAME = "answer"

logger = logging.getLogger(__name__)


class SemanticKernelChatProvider:
    """Grounded knowledge-base assistant backed by a Semantic Kernel prompt function."""

    provider_name = "semantic-kernel"

    def __init__(self, *, async_client: AsyncOpenAI | None = None) -> None:
        settings = get_settings()
        self._api_key = settings.ai_api_key
        self._base_url = settings.ai_base_url.rstrip("/") or "https://api.openai.com/v1"
        self._model = settings.ai_model
        self._client = async_client
        self._kernel: Kernel | None = None
        self._function: Any = None

    @property
    def prompt_version(self) -> str:
        return CHAT_PROMPT_VERSION

    def _require_key(self) -> None:
        if not self._api_key and self._client is None:
            raise ServiceUnavailableError("AI chat is not configured. Set AI_API_KEY to enable it.")

    def _build_kernel(self) -> None:
        client = self._client or AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=60.0,
            max_retries=1,
        )
        service = OpenAIChatCompletion(
            ai_model_id=self._model,
            async_client=client,
            service_id=SERVICE_ID,
        )
        kernel = Kernel()
        kernel.add_service(service)
        self._function = kernel.add_function(
            plugin_name=PLUGIN_NAME,
            function_name=FUNCTION_NAME,
            description=(
                "Answers a question about Reyvo using only the supplied knowledge-base sources."
            ),
            prompt=CHAT_PROMPT_TEMPLATE,
            template_format="semantic-kernel",
            prompt_execution_settings=OpenAIChatPromptExecutionSettings(
                service_id=SERVICE_ID,
                temperature=0.2,
                max_tokens=800,
            ),
        )
        self._kernel = kernel

    async def answer(self, *, question: str, context: str, history: str = "") -> ChatResult:
        self._require_key()
        if self._kernel is None or self._function is None:
            self._build_kernel()
        assert self._kernel is not None and self._function is not None

        try:
            result = await self._kernel.invoke(
                self._function,
                KernelArguments(question=question, context=context, history=history or "None"),
            )
        except ServiceUnavailableError:
            raise
        except Exception as exc:  # pragma: no cover - exercised via provider failure
            logger.warning("Chat provider request failed.", exc_info=True)
            raise ServiceUnavailableError(
                "The AI provider could not answer the request right now."
            ) from exc

        answer = str(result).strip()
        if not answer:
            raise ServiceUnavailableError("The AI provider returned an empty answer.")

        prompt_tokens, completion_tokens = _extract_usage(result)
        return ChatResult(
            answer=answer,
            model=self._model,
            prompt_version=self.prompt_version,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _extract_usage(result: object) -> tuple[int | None, int | None]:
    """Pull token usage out of the Semantic Kernel function metadata."""

    metadata = getattr(result, "metadata", None) or {}
    if not isinstance(metadata, dict):
        return None, None

    candidates: list[object] = []
    nested = metadata.get("metadata")
    if isinstance(nested, list):
        candidates.extend(nested)
    elif nested is not None:
        candidates.append(nested)
    candidates.append(metadata)

    for candidate in candidates:
        usage = (
            candidate.get("usage")
            if isinstance(candidate, dict)
            else getattr(candidate, "usage", None)
        )
        if usage is None:
            continue
        prompt = (
            usage.get("prompt_tokens")
            if isinstance(usage, dict)
            else getattr(usage, "prompt_tokens", None)
        )
        completion = (
            usage.get("completion_tokens")
            if isinstance(usage, dict)
            else getattr(usage, "completion_tokens", None)
        )
        return _as_int(prompt), _as_int(completion)
    return None, None
