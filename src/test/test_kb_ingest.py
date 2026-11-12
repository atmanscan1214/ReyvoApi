from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from docx import Document as WordDocument
from reyvo.core.utils import utcnow
from reyvo.db.base import Base
from reyvo.db.models import ChatConversation, ChatMessage, KnowledgeChunk, KnowledgeDocument
from reyvo.integrations.ai.base import ChatResult
from reyvo.integrations.ai.factory import set_chat_provider, set_embedding_provider
from reyvo.integrations.ai.prompts import CHAT_PROMPT_VERSION, CHAT_REFUSAL_TEXT
from reyvo.services.knowledge_service import (
    REFUSAL_MESSAGE,
    answer_question,
    ingest_document,
    list_messages,
    purge_conversations_before,
    search_chunks,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


class FakeEmbeddings:
    available = True

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[float(index), 1.0, 0.0] for index, _text in enumerate(texts)]


class FakeChatProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def answer(self, *, question: str, context: str, history: str) -> ChatResult:
        self.calls.append({"question": question, "context": context, "history": history})
        return ChatResult(
            answer="Tenant isolation uses PostgreSQL row level security [1].",
            model="fake-chat",
            prompt_version=CHAT_PROMPT_VERSION,
            prompt_tokens=10,
            completion_tokens=6,
        )


class RefusingChatProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def answer(self, *, question: str, context: str, history: str) -> ChatResult:
        self.calls.append({"question": question, "context": context, "history": history})
        return ChatResult(
            answer=CHAT_REFUSAL_TEXT,
            model="fake-chat",
            prompt_version=CHAT_PROMPT_VERSION,
        )


class InventedCitationChatProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def answer(self, *, question: str, context: str, history: str) -> ChatResult:
        self.calls.append({"question": question, "context": context, "history": history})
        return ChatResult(
            answer="Tenant isolation uses PostgreSQL row level security [9].",
            model="fake-chat",
            prompt_version=CHAT_PROMPT_VERSION,
        )


class UncitedThenCitedChatProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def answer(self, *, question: str, context: str, history: str) -> ChatResult:
        self.calls.append({"question": question, "context": context, "history": history})
        if len(self.calls) == 1:
            return ChatResult(
                answer="Tenant isolation uses PostgreSQL row level security.",
                model="fake-chat",
                prompt_version=CHAT_PROMPT_VERSION,
            )
        return ChatResult(
            answer="Tenant isolation uses PostgreSQL row level security [1].",
            model="fake-chat",
            prompt_version=CHAT_PROMPT_VERSION,
        )


class UncitedChatProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def answer(self, *, question: str, context: str, history: str) -> ChatResult:
        self.calls.append({"question": question, "context": context, "history": history})
        return ChatResult(
            answer="Tenant isolation uses PostgreSQL row level security.",
            model="fake-chat",
            prompt_version=CHAT_PROMPT_VERSION,
        )


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


def _build_docx(tmp_path: Path) -> Path:
    document = WordDocument()
    document.add_heading("5. Multi-Tenancy", level=1)
    document.add_paragraph("Tenant isolation uses PostgreSQL row level security.")
    document.add_heading("7.4 Campaign builder", level=1)
    document.add_paragraph("Campaigns support up to eight steps.")
    path = tmp_path / "design.docx"
    document.save(path)
    return path


async def test_ingest_document_creates_chunks_and_embeddings(session, tmp_path):
    fake = FakeEmbeddings()
    set_embedding_provider(fake)
    path = _build_docx(tmp_path)

    result = await ingest_document(session, path=path, slug="test-doc", title="Reyvo Test Design")

    assert result.skipped is False
    assert result.title == "Reyvo Test Design"
    assert result.chunk_count >= 2
    assert result.embedded_chunk_count == result.chunk_count
    assert fake.batches and len(fake.batches[0]) == result.chunk_count

    document = await session.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.slug == "test-doc")
    )
    assert document is not None
    assert document.checksum
    assert document.source_path == str(path)

    chunks = list(
        (
            await session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document.id)
                .order_by(KnowledgeChunk.ordinal)
            )
        ).all()
    )
    assert len(chunks) == result.chunk_count
    assert chunks[0].embedding is not None
    assert any("row level security" in chunk.content for chunk in chunks)
    assert all(chunk.heading_path for chunk in chunks)


async def test_ingest_is_idempotent_unless_forced(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    path = _build_docx(tmp_path)

    first = await ingest_document(session, path=path, slug="test-doc")
    second = await ingest_document(session, path=path, slug="test-doc")
    forced = await ingest_document(session, path=path, slug="test-doc", force=True)

    assert first.skipped is False
    assert second.skipped is True
    assert forced.skipped is False
    assert forced.chunk_count == first.chunk_count


async def test_ingest_backfills_embeddings_added_later(session, tmp_path):
    path = _build_docx(tmp_path)

    first = await ingest_document(session, path=path, slug="test-doc")
    assert first.embedded_chunk_count == 0

    set_embedding_provider(FakeEmbeddings())
    backfilled = await ingest_document(session, path=path, slug="test-doc")

    assert backfilled.skipped is False
    assert backfilled.embedded_chunk_count == backfilled.chunk_count


async def test_search_chunks_finds_the_relevant_section(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)

    results = await search_chunks(session, "row level security", limit=3)

    assert results
    assert "row level security" in results[0].content.lower()
    assert results[0].heading_path == "5. Multi-Tenancy"


async def test_answer_question_persists_turns_and_uses_history(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)
    provider = FakeChatProvider()
    set_chat_provider(provider)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    exchange = await answer_question(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        message="How is tenant isolation enforced?",
    )

    assert exchange.used_context is True
    assert exchange.citations
    assert exchange.model == "fake-chat"
    assert "row level security" in provider.calls[0]["context"].lower()
    assert provider.calls[0]["history"] == ""

    follow_up = await answer_question(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        message="How does row level security work?",
        conversation_id=exchange.conversation_id,
    )

    assert follow_up.conversation_id == exchange.conversation_id
    assert (
        "Assistant: Tenant isolation uses PostgreSQL row level security [1]."
        in (provider.calls[1]["history"])
    )

    messages = await list_messages(session, exchange.conversation_id)
    assert len(messages) == 4
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert isinstance(messages[1], ChatMessage)
    assert messages[1].citations[0]["heading_path"] == "5. Multi-Tenancy"
    assert messages[1].prompt_tokens == 10


async def test_answer_question_refuses_without_matching_context(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)
    provider = FakeChatProvider()
    set_chat_provider(provider)

    exchange = await answer_question(
        session,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        message="quantum flux capacitor",
    )

    assert exchange.used_context is False
    assert exchange.answer == REFUSAL_MESSAGE
    assert exchange.citations == []
    assert provider.calls == []


async def test_answer_question_resolves_chained_follow_ups(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)
    provider = FakeChatProvider()
    set_chat_provider(provider)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    first = await answer_question(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        message="How is tenant isolation enforced?",
    )
    second = await answer_question(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        message="tell me more",
        conversation_id=first.conversation_id,
    )
    third = await answer_question(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        message="why?",
        conversation_id=first.conversation_id,
    )

    assert second.used_context is True
    assert third.used_context is True
    assert "row level security" in provider.calls[1]["context"].lower()
    assert "row level security" in provider.calls[2]["context"].lower()
    # The later follow-up is resolved past "tell me more" to the original topic.
    assert "tell me more" in provider.calls[2]["history"].lower()


async def test_follow_ups_fall_back_to_the_conversation_title(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)
    provider = FakeChatProvider()
    set_chat_provider(provider)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conversation = ChatConversation(
        tenant_id=tenant_id,
        user_id=user_id,
        title="How is tenant isolation enforced?",
    )
    session.add(conversation)
    await session.flush()
    started = utcnow()
    for turn in range(5):
        session.add(
            ChatMessage(
                conversation_id=conversation.id,
                role="user",
                content="tell me more",
                created_at=started + timedelta(seconds=turn * 2),
            )
        )
        session.add(
            ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="More detail about tenant isolation [1].",
                created_at=started + timedelta(seconds=turn * 2 + 1),
            )
        )
    await session.commit()

    exchange = await answer_question(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        message="why?",
        conversation_id=conversation.id,
    )

    assert exchange.used_context is True
    assert "row level security" in provider.calls[0]["context"].lower()


async def test_answer_question_does_not_reuse_context_for_a_new_topic(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)
    provider = FakeChatProvider()
    set_chat_provider(provider)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    first = await answer_question(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        message="How is tenant isolation enforced?",
    )
    off_topic = await answer_question(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        message="What is the capital of France?",
        conversation_id=first.conversation_id,
    )

    assert first.used_context is True
    assert off_topic.used_context is False
    assert off_topic.answer == REFUSAL_MESSAGE
    assert off_topic.citations == []
    # The model is not called with the previous topic as if it answered this one.
    assert len(provider.calls) == 1


async def test_answer_question_refuses_context_follow_up_without_history(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)
    provider = FakeChatProvider()
    set_chat_provider(provider)

    exchange = await answer_question(
        session,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        message="tell me more",
    )

    assert exchange.used_context is False
    assert exchange.answer == REFUSAL_MESSAGE
    assert provider.calls == []


async def test_answer_question_normalizes_model_refusals(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)
    set_chat_provider(RefusingChatProvider())

    exchange = await answer_question(
        session,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        message="How is tenant isolation enforced?",
    )

    assert exchange.answer == REFUSAL_MESSAGE
    assert exchange.citations == []
    assert exchange.used_context is False


async def test_answer_question_rejects_invented_citations(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)
    set_chat_provider(InventedCitationChatProvider())

    exchange = await answer_question(
        session,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        message="How is tenant isolation enforced?",
    )

    assert exchange.answer == REFUSAL_MESSAGE
    assert exchange.citations == []
    assert exchange.used_context is False


async def test_answer_question_repairs_an_uncited_answer(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)
    provider = UncitedThenCitedChatProvider()
    set_chat_provider(provider)

    exchange = await answer_question(
        session,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        message="How is tenant isolation enforced?",
    )

    assert exchange.used_context is True
    assert exchange.answer.endswith("[1].")
    assert exchange.citations
    assert len(provider.calls) == 2
    assert "did not cite" in provider.calls[1]["question"]


async def test_answer_question_refuses_when_repair_is_still_uncited(session, tmp_path):
    set_embedding_provider(FakeEmbeddings())
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")
    set_embedding_provider(None)
    provider = UncitedChatProvider()
    set_chat_provider(provider)

    exchange = await answer_question(
        session,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        message="How is tenant isolation enforced?",
    )

    assert exchange.answer == REFUSAL_MESSAGE
    assert exchange.citations == []
    assert exchange.used_context is False
    assert len(provider.calls) == 2


class FailingEmbeddings:
    available = True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding provider unavailable")


async def test_ingest_survives_embedding_failure(session, tmp_path):
    set_embedding_provider(FailingEmbeddings())

    result = await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")

    assert result.skipped is False
    assert result.chunk_count >= 2
    assert result.embedded_chunk_count == 0

    set_embedding_provider(None)
    results = await search_chunks(session, "row level security", limit=3)
    assert results


async def test_search_chunks_returns_empty_for_blank_query(session, tmp_path):
    await ingest_document(session, path=_build_docx(tmp_path), slug="test-doc")

    assert await search_chunks(session, "   \n\t ") == []


async def test_purge_conversations_before_removes_expired_history(session):
    now = utcnow()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    old = ChatConversation(
        tenant_id=tenant_id,
        user_id=user_id,
        title="old",
        updated_at=now - timedelta(days=120),
    )
    recent = ChatConversation(
        tenant_id=tenant_id,
        user_id=user_id,
        title="recent",
        updated_at=now - timedelta(days=5),
    )
    session.add_all([old, recent])
    await session.flush()
    session.add_all(
        [
            ChatMessage(conversation_id=old.id, role="user", content="old"),
            ChatMessage(conversation_id=recent.id, role="user", content="recent"),
        ]
    )
    await session.commit()

    deleted = await purge_conversations_before(session, now - timedelta(days=90))

    assert deleted == 1
    remaining = list((await session.scalars(select(ChatConversation))).all())
    assert [conversation.id for conversation in remaining] == [recent.id]
    remaining_messages = list((await session.scalars(select(ChatMessage))).all())
    assert [message.conversation_id for message in remaining_messages] == [recent.id]
