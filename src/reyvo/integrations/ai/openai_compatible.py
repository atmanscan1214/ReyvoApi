from __future__ import annotations

import json

import httpx

from reyvo.core.config import get_settings
from reyvo.core.exceptions import ServiceUnavailableError
from reyvo.integrations.ai.base import DraftRequest, DraftResult

SYSTEM_PROMPT = """You write concise, professional outbound emails for a staffing agency.
Rules:
- Never invent facts about the recipient or the agency.
- Keep the email under 130 words.
- Use a friendly, consultative tone.
- End with one clear, low-friction call to action.
- Return ONLY valid JSON with keys: subject, body_text, body_html.
- body_html must be simple HTML using <p> tags and no scripts or images.
"""


class OpenAICompatibleDraftProvider:
    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.ai_api_key
        self._base_url = settings.ai_base_url.rstrip("/")
        self._model = settings.ai_model

    def _require_key(self) -> None:
        if not self._api_key:
            raise ServiceUnavailableError(
                "AI drafting is not configured. Set AI_API_KEY to enable it."
            )

    async def draft(self, request: DraftRequest) -> DraftResult:
        self._require_key()
        payload = {
            "model": self._model,
            "temperature": 0.6,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._user_prompt(request)},
            ],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            raise ServiceUnavailableError(
                "The AI provider rejected the drafting request.",
                details={"provider_status": response.status_code},
            )
        content = response.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        body_text = str(data.get("body_text") or "").strip()
        body_html = str(data.get("body_html") or "").strip()
        if not body_html and body_text:
            paragraphs = [line for line in body_text.split("\n\n") if line.strip()]
            body_html = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
        return DraftResult(
            subject=str(data.get("subject") or "").strip(),
            body_html=body_html,
            body_text=body_text,
            model=self._model,
        )

    @staticmethod
    def _user_prompt(request: DraftRequest) -> str:
        details = {
            "agency_name": request.agency_name,
            "contact_name": request.contact_name,
            "contact_title": request.contact_title,
            "company": request.company,
            "industry": request.industry,
            "step_order": request.step_order,
            "tone": request.tone,
            "value_proposition": request.value_proposition,
            "call_to_action": request.call_to_action,
            "extra_instructions": request.extra_instructions,
            "previous_subjects": request.previous_subjects,
        }
        return (
            "Write an outbound email for this context. "
            "Use merge placeholders only when a value is missing.\n"
            f"{json.dumps(details, ensure_ascii=False)}"
        )
