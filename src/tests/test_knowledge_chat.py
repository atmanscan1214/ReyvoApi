from __future__ import annotations

import uuid

import pytest
from reyvo.core.config import get_settings, set_settings
from reyvo.core.utils import utcnow
from reyvo.db.models import ChatConversation, ChatMessage, KnowledgeChunk, KnowledgeDocument
from reyvo.integrations.ai.base import ChatResult
from reyvo.integrations.ai.factory import reset_ai_providers, set_chat_provider
from reyvo.integrations.ai.prompts import CHAT_PROMPT_VERSION, CHAT_REFUSAL_TEXT
from reyvo.services.knowledge_service import REFUSAL_MESSAGE
from sqlalchemy import func, select


class FakeChatProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def answer(self, *, question: str, context: str, history: str) -> ChatResult:
        self.calls.append({"question": question, "context": context, "history": history})
        return ChatResult(
            answer="Tenant isolation uses PostgreSQL row level security [1].",
            model="fake-chat",
            prompt_version=CHAT_PROMPT_VERSION,
            prompt_tokens=12,
            completion_tokens=6,
        )


class SecondSourceChatProvider(FakeChatProvider):
    async def answer(self, *, question: str, context: str, history: str) -> ChatResult:
        self.calls.append({"question": question, "context": context, "history": history})
        return ChatResult(
            answer="Campaigns support up to eight steps [2].",
            model="fake-chat",
            prompt_version=CHAT_PROMPT_VERSION,
            prompt_tokens=12,
            completion_tokens=6,
        )


class RefusingChatProvider(FakeChatProvider):
    async def answer(self, *, question: str, context: str, history: str) -> ChatResult:
        self.calls.append({"question": question, "context": context, "history": history})
        return ChatResult(
            answer=CHAT_REFUSAL_TEXT,
            model="fake-chat",
            prompt_version=CHAT_PROMPT_VERSION,
        )


class InventedCitationChatProvider(FakeChatProvider):
    async def answer(self, *, question: str, context: str, history: str) -> ChatResult:
        self.calls.append({"question": question, "context": context, "history": history})
        return ChatResult(
            answer="Tenant isolation uses PostgreSQL row level security [9].",
            model="fake-chat",
            prompt_version=CHAT_PROMPT_VERSION,
        )


@pytest.fixture(autouse=True)
def _reset_providers():
    yield
    reset_ai_providers()


async def _seed_knowledge(app) -> uuid.UUID:
    async with app.state.session_factory() as session:
        document = KnowledgeDocument(
            slug="reyvo-design-document",
            title="Reyvo Detailed Design Document",
            source_path="Reyvo_Detailed_Design_Document_v1.0.docx",
            version="1.0",
            checksum="a" * 64,
            parser_version="test",
            status="active",
            doc_metadata={},
            chunk_count=2,
            embedded_chunk_count=0,
            ingested_at=utcnow(),
        )
        session.add(document)
        await session.flush()
        session.add_all(
            [
                KnowledgeChunk(
                    document_id=document.id,
                    ordinal=0,
                    heading_path="5. Multi-Tenancy > 5.1 Tenant isolation model",
                    anchor="5-1-tenant-isolation-model",
                    content=(
                        "Tenant isolation uses PostgreSQL row level security and "
                        "application-scoped queries."
                    ),
                    token_count=20,
                    chunk_metadata={},
                ),
                KnowledgeChunk(
                    document_id=document.id,
                    ordinal=1,
                    heading_path="7.4 Campaign builder",
                    anchor="7-4-campaign-builder",
                    content=(
                        "Campaigns support up to eight steps with configurable delays "
                        "and AI personalization."
                    ),
                    token_count=20,
                    chunk_metadata={},
                ),
            ]
        )
        await session.commit()
        return document.id


async def test_chat_answers_with_citations_and_persists_conversation(
    app, client, register_account, auth_headers
):
    await _seed_knowledge(app)
    provider = FakeChatProvider()
    set_chat_provider(provider)

    tokens = await register_account()
    headers = auth_headers(tokens)

    response = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["used_context"] is True
    assert body["model"] == "fake-chat"
    assert body["citations"][0]["index"] == 1
    assert body["citations"][0]["heading_path"].startswith("5. Multi-Tenancy")
    assert "row level security" in provider.calls[0]["context"].lower()

    conversation_id = body["conversation_id"]
    follow_up = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={
            "message": "How does row level security help?",
            "conversation_id": conversation_id,
        },
    )

    assert follow_up.status_code == 200, follow_up.text
    assert follow_up.json()["conversation_id"] == conversation_id
    assert (
        "Assistant: Tenant isolation uses PostgreSQL row level security [1]."
        in (provider.calls[1]["history"])
    )

    detail = await client.get(f"/api/v1/ai/conversations/{conversation_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    messages = detail.json()["messages"]
    assert len(messages) == 4
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert messages[1]["citations"][0]["anchor"] == "5-1-tenant-isolation-model"


async def test_chat_refuses_when_no_context_matches(app, client, register_account, auth_headers):
    await _seed_knowledge(app)
    provider = FakeChatProvider()
    set_chat_provider(provider)

    tokens = await register_account()
    headers = auth_headers(tokens)

    response = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "quantum flux capacitor"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["used_context"] is False
    assert body["answer"] == REFUSAL_MESSAGE
    assert body["citations"] == []
    assert provider.calls == []


async def test_chat_resolves_context_only_follow_ups(app, client, register_account, auth_headers):
    await _seed_knowledge(app)
    provider = FakeChatProvider()
    set_chat_provider(provider)
    tokens = await register_account()
    headers = auth_headers(tokens)

    first = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?"},
    )
    follow_up = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={
            "message": "tell me more",
            "conversation_id": first.json()["conversation_id"],
        },
    )

    assert follow_up.status_code == 200, follow_up.text
    body = follow_up.json()
    assert body["used_context"] is True
    assert "row level security" in provider.calls[1]["context"].lower()
    assert "Assistant:" in provider.calls[1]["history"]


async def test_chat_does_not_answer_a_new_topic_from_previous_context(
    app, client, register_account, auth_headers
):
    await _seed_knowledge(app)
    provider = FakeChatProvider()
    set_chat_provider(provider)
    tokens = await register_account()
    headers = auth_headers(tokens)

    first = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?"},
    )
    off_topic = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={
            "message": "What is the capital of France?",
            "conversation_id": first.json()["conversation_id"],
        },
    )

    assert off_topic.status_code == 200, off_topic.text
    body = off_topic.json()
    assert body["used_context"] is False
    assert body["answer"] == REFUSAL_MESSAGE
    assert body["citations"] == []
    assert len(provider.calls) == 1


async def test_chat_normalizes_out_of_scope_model_answers(
    app, client, register_account, auth_headers
):
    await _seed_knowledge(app)
    set_chat_provider(RefusingChatProvider())
    tokens = await register_account()
    headers = auth_headers(tokens)

    response = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == REFUSAL_MESSAGE
    assert body["citations"] == []
    assert body["used_context"] is False


async def test_chat_rejects_invented_citations(app, client, register_account, auth_headers):
    await _seed_knowledge(app)
    set_chat_provider(InventedCitationChatProvider())
    tokens = await register_account()
    headers = auth_headers(tokens)

    response = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == REFUSAL_MESSAGE
    assert body["citations"] == []
    assert body["used_context"] is False


async def test_chat_requires_api_key(app, client, register_account, auth_headers):
    await _seed_knowledge(app)
    reset_ai_providers()

    tokens = await register_account()
    headers = auth_headers(tokens)

    response = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?"},
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "service_unavailable"


async def test_conversations_are_listed_and_deleted(app, client, register_account, auth_headers):
    await _seed_knowledge(app)
    set_chat_provider(FakeChatProvider())

    tokens = await register_account()
    headers = auth_headers(tokens)
    created = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?"},
    )
    conversation_id = created.json()["conversation_id"]

    listing = await client.get("/api/v1/ai/conversations", headers=headers)
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == conversation_id

    deleted = await client.delete(f"/api/v1/ai/conversations/{conversation_id}", headers=headers)
    assert deleted.status_code == 204

    missing = await client.get(f"/api/v1/ai/conversations/{conversation_id}", headers=headers)
    assert missing.status_code == 404

    async with app.state.session_factory() as session:
        remaining = await session.scalar(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.conversation_id == uuid.UUID(conversation_id))
        )
    assert remaining == 0


async def test_conversations_are_scoped_to_user_and_tenant(
    app, client, register_account, auth_headers
):
    await _seed_knowledge(app)
    set_chat_provider(FakeChatProvider())

    owner_tokens = await register_account()
    owner_headers = auth_headers(owner_tokens)
    created = await client.post(
        "/api/v1/ai/chat",
        headers=owner_headers,
        json={"message": "How is tenant isolation enforced?"},
    )
    conversation_id = created.json()["conversation_id"]

    other_tokens = await register_account(email="other@example.com", agency_name="Other Staffing")
    other_headers = auth_headers(other_tokens)

    response = await client.get(
        f"/api/v1/ai/conversations/{conversation_id}", headers=other_headers
    )

    assert response.status_code == 404


async def test_conversations_are_scoped_to_user_within_tenant(
    app, client, register_account, auth_headers
):
    tokens = await register_account()
    headers = auth_headers(tokens)
    tenant_id = uuid.UUID(tokens["tenant"]["id"])

    async with app.state.session_factory() as session:
        foreign = ChatConversation(
            tenant_id=tenant_id,
            user_id=uuid.uuid4(),
            title="Another member's conversation",
        )
        session.add(foreign)
        await session.commit()
        foreign_id = foreign.id

    detail = await client.get(f"/api/v1/ai/conversations/{foreign_id}", headers=headers)
    assert detail.status_code == 404

    listing = await client.get("/api/v1/ai/conversations", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


async def test_chat_requires_platform_admin_by_default(app, client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)
    original = get_settings()
    set_settings(original.model_copy(update={"knowledge_chat_access": "platform_admin"}))
    try:
        chat = await client.post(
            "/api/v1/ai/chat",
            headers=headers,
            json={"message": "How is tenant isolation enforced?"},
        )
        assert chat.status_code == 403, chat.text
        assert chat.json()["error"]["code"] == "forbidden"

        listing = await client.get("/api/v1/ai/conversations", headers=headers)
        assert listing.status_code == 403

        search = await client.post(
            "/api/v1/ai/knowledge/search",
            headers=headers,
            json={"query": "tenant isolation"},
        )
        assert search.status_code == 403
    finally:
        set_settings(original)


async def test_citations_only_include_sources_the_answer_references(
    app, client, register_account, auth_headers
):
    await _seed_knowledge(app)
    set_chat_provider(SecondSourceChatProvider())
    tokens = await register_account()
    headers = auth_headers(tokens)

    response = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "tenant isolation campaign builder"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [citation["index"] for citation in body["citations"]] == [2]
    assert body["citations"][0]["heading_path"] == "7.4 Campaign builder"


async def test_conversation_detail_respects_message_limit(
    app, client, register_account, auth_headers
):
    await _seed_knowledge(app)
    set_chat_provider(FakeChatProvider())
    tokens = await register_account()
    headers = auth_headers(tokens)
    created = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?"},
    )
    conversation_id = created.json()["conversation_id"]

    detail = await client.get(
        f"/api/v1/ai/conversations/{conversation_id}?message_limit=1", headers=headers
    )

    assert detail.status_code == 200, detail.text
    messages = detail.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"


async def test_knowledge_search_and_status(app, client, register_account, auth_headers):
    await _seed_knowledge(app)
    tokens = await register_account()
    headers = auth_headers(tokens)

    search = await client.post(
        "/api/v1/ai/knowledge/search",
        headers=headers,
        json={"query": "row level security", "limit": 3},
    )
    assert search.status_code == 200, search.text
    assert search.json()["results"][0]["anchor"] == "5-1-tenant-isolation-model"

    status = await client.get("/api/v1/ai/knowledge/status", headers=headers)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["chunk_count"] == 2
    assert body["embedded_chunk_count"] == 0
    assert body["documents"][0]["slug"] == "reyvo-design-document"
    assert body["documents"][0]["embedding_model"] is None
    assert body["documents"][0]["embeddings_stale"] is False


async def test_reindex_requires_platform_admin(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)

    response = await client.post("/api/v1/ai/knowledge/reindex", headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_reindex_can_be_queued(app, client, monkeypatch):
    from reyvo.api.deps import require_platform_admin

    async def _admin() -> object:
        return object()

    async def _enqueue(name: str, *args: object, **kwargs: object) -> str:
        assert name == "reindex_design_document"
        assert kwargs == {"force": True}
        return "job-123"

    app.dependency_overrides[require_platform_admin] = _admin
    monkeypatch.setattr("reyvo.api.v1.ai.enqueue_job", _enqueue)
    try:
        response = await client.post(
            "/api/v1/ai/knowledge/reindex",
            json={"force": True, "background": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "queued", "job_id": "job-123"}


async def test_chat_rate_limit_returns_429(app, client, register_account, auth_headers):
    await _seed_knowledge(app)
    set_chat_provider(FakeChatProvider())
    tokens = await register_account()
    headers = auth_headers(tokens)
    original = get_settings()
    set_settings(original.model_copy(update={"ai_chat_rate_limit_per_minute": 1}))
    try:
        first = await client.post(
            "/api/v1/ai/chat",
            headers=headers,
            json={"message": "How is tenant isolation enforced?"},
        )
        second = await client.post(
            "/api/v1/ai/chat",
            headers=headers,
            json={"message": "How is tenant isolation enforced?"},
        )
    finally:
        set_settings(original)

    assert first.status_code == 200, first.text
    assert second.status_code == 429, second.text
    body = second.json()["error"]
    assert body["code"] == "rate_limited"
    assert body["details"]["retry_after_seconds"] >= 1


async def test_chat_rejects_empty_message(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)

    response = await client.post("/api/v1/ai/chat", headers=headers, json={"message": ""})

    assert response.status_code == 422


async def test_chat_top_k_limits_retrieved_sources(app, client, register_account, auth_headers):
    await _seed_knowledge(app)
    provider = FakeChatProvider()
    set_chat_provider(provider)
    tokens = await register_account()
    headers = auth_headers(tokens)

    response = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?", "top_k": 1},
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["citations"]) == 1
    assert "[2]" not in provider.calls[0]["context"]


async def test_chat_rejects_out_of_range_top_k(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)

    response = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?", "top_k": 50},
    )

    assert response.status_code == 422


async def test_conversation_rows_are_tenant_scoped(app, client, register_account, auth_headers):
    await _seed_knowledge(app)
    set_chat_provider(FakeChatProvider())
    tokens = await register_account()
    headers = auth_headers(tokens)
    created = await client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "How is tenant isolation enforced?"},
    )
    conversation_id = uuid.UUID(created.json()["conversation_id"])

    async with app.state.session_factory() as session:
        conversation = await session.get(ChatConversation, conversation_id)
        assert conversation is not None
        assert str(conversation.tenant_id) == tokens["tenant"]["id"]
