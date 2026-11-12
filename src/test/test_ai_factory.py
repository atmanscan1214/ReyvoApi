from __future__ import annotations

from reyvo.integrations.ai.base import ChatResult
from reyvo.integrations.ai.factory import (
    get_chat_provider,
    get_draft_provider,
    get_embedding_provider,
    reset_ai_providers,
    set_chat_provider,
)


class StubChatProvider:
    async def answer(self, *, question: str, context: str, history: str) -> ChatResult:
        return ChatResult(answer="stub", model="stub")


def test_default_providers_are_cached_per_process():
    reset_ai_providers()

    draft = get_draft_provider()
    chat = get_chat_provider()
    embeddings = get_embedding_provider()

    assert get_draft_provider() is draft
    assert get_chat_provider() is chat
    assert get_embedding_provider() is embeddings

    reset_ai_providers()

    assert get_chat_provider() is not chat


def test_override_wins_until_reset():
    reset_ai_providers()
    stub = StubChatProvider()
    set_chat_provider(stub)

    assert get_chat_provider() is stub

    reset_ai_providers()

    assert get_chat_provider() is not stub
