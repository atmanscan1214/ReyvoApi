"""Semantic Kernel based email drafting.

The kernel owns the provider configuration (model, endpoint, credentials) and a
single prompt function that turns a :class:`DraftRequest` into a personalized
email. Candidate facts travel in the ``profile`` argument; campaign settings
travel in ``context``. Nothing from the profile is interpolated into the
template source itself, so profile text cannot alter the prompt structure.
"""

from __future__ import annotations

import html
import json
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
from reyvo.integrations.ai.base import DraftRequest, DraftResult
from reyvo.integrations.ai.prompts import (
    PROMPT_VERSION,
    USER_PROMPT_TEMPLATE,
    build_prompt_payload,
)

SERVICE_ID = "reyvo-outreach"
PLUGIN_NAME = "outreach"
FUNCTION_NAME = "draft_email"


def extract_json_object(content: str) -> dict[str, Any]:
    """Best-effort JSON extraction from a model response."""

    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -len("```")]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ServiceUnavailableError(
                "The AI provider returned an unreadable draft.",
                details={"preview": content[:200]},
            ) from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ServiceUnavailableError(
                "The AI provider returned an unreadable draft.",
                details={"preview": content[:200]},
            ) from exc
    if not isinstance(data, dict):
        raise ServiceUnavailableError(
            "The AI provider returned an unexpected draft shape.",
            details={"preview": content[:200]},
        )
    return data


def parse_draft_response(
    content: str, *, model: str, prompt_version: str = PROMPT_VERSION
) -> DraftResult:
    data = extract_json_object(content)

    subject = str(data.get("subject") or "").strip()
    body_text = str(data.get("body_text") or "").strip()
    body_html = str(data.get("body_html") or "").strip()

    if not body_html and body_text:
        paragraphs = [line.strip() for line in body_text.split("\n\n") if line.strip()]
        body_html = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)

    if not subject or not body_text:
        missing = [
            key for key, value in {"subject": subject, "body_text": body_text}.items() if not value
        ]
        raise ServiceUnavailableError(
            "The AI provider returned an incomplete draft.",
            details={"missing": missing},
        )

    return DraftResult(
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        model=model,
        prompt_version=prompt_version,
    )


class SemanticKernelDraftProvider:
    """Draft provider backed by a Semantic Kernel prompt function."""

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
        return PROMPT_VERSION

    def _require_key(self) -> None:
        if not self._api_key and self._client is None:
            raise ServiceUnavailableError(
                "AI drafting is not configured. Set AI_API_KEY to enable it."
            )

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
                "Drafts a personalized outbound recruiting email for one prospective "
                "employee, using only facts from the supplied profile."
            ),
            prompt=USER_PROMPT_TEMPLATE,
            template_format="semantic-kernel",
            prompt_execution_settings=OpenAIChatPromptExecutionSettings(
                service_id=SERVICE_ID,
                temperature=0.6,
                max_tokens=800,
                response_format={"type": "json_object"},
            ),
        )
        self._kernel = kernel

    async def draft(self, request: DraftRequest) -> DraftResult:
        self._require_key()
        if self._kernel is None or self._function is None:
            self._build_kernel()
        assert self._kernel is not None and self._function is not None

        profile, context = build_prompt_payload(request)
        try:
            result = await self._kernel.invoke(
                self._function,
                KernelArguments(profile=profile, context=context),
            )
        except ServiceUnavailableError:
            raise
        except Exception as exc:  # pragma: no cover - exercised via provider failure
            raise ServiceUnavailableError(
                "The AI provider rejected the drafting request.",
                details={"reason": str(exc)[:300]},
            ) from exc

        return parse_draft_response(
            str(result),
            model=self._model,
            prompt_version=self.prompt_version,
        )
