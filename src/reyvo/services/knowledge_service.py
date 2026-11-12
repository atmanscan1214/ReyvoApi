"""Database-backed knowledge base, hybrid retrieval and grounded chat.

The design document is ingested once into ``knowledge_documents`` /
``knowledge_chunks``. Chat questions are answered from the most relevant chunks
with citations, and every conversation is persisted per tenant and user.

Retrieval combines BM25 lexical ranking with optional embedding similarity
(Reciprocal Rank Fusion). Embeddings are an enhancement, not a requirement: if
``AI_EMBEDDING_MODEL`` is empty or the embedding request fails, lexical search
continues to work.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.config import get_settings
from reyvo.core.exceptions import BadRequestError, ConflictError, NotFoundError
from reyvo.core.utils import utcnow
from reyvo.db.models import (
    ChatConversation,
    ChatMessage,
    KnowledgeChunk,
    KnowledgeDocument,
)
from reyvo.integrations.ai.factory import get_chat_provider, get_embedding_provider
from reyvo.integrations.ai.prompts import CHAT_REFUSAL_TEXT
from reyvo.integrations.knowledge.docx_parser import (
    ParsedSection,
    parse_docx,
)

logger = logging.getLogger("reyvo.knowledge")

PARSER_VERSION = "docx-heading-v1"
DESIGN_DOC_SLUG = "reyvo-design-document"
DESIGN_DOC_TITLE = "Reyvo Detailed Design Document"

MAX_TOP_K = 12
MAX_CONTEXT_CHARS = 12_000
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_CHARS = 4_000
MAX_CONVERSATION_MESSAGES = 200
MAX_RANKED_CANDIDATES = 50
RRF_K = 60
MIN_VECTOR_SCORE = 0.25
BM25_K1 = 1.5
BM25_B = 0.75
MIN_PREFIX_LENGTH = 4

REFUSAL_MESSAGE = (
    f"{CHAT_REFUSAL_TEXT} Try rephrasing the question, or ask about a section such as "
    "architecture, multi-tenancy, campaigns, deliverability, or compliance."
)

_REPAIR_INSTRUCTION = (
    "{question}\n\n"
    "Your previous answer did not cite the knowledge-base sources. Answer the question "
    "again using only the supplied sources and cite every factual sentence with [n]."
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+.#/\-]*")
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
_WHITESPACE_RE = re.compile(r"\s+")
_DEICTIC_RE = re.compile(
    r"\b(?:that|this|it|they|them|those|these|same|former|latter|above)\b",
    re.IGNORECASE,
)
_FOLLOW_UP_RE = re.compile(
    r"^(?:"
    r"tell me more|more(?: detail| details| information)?|go on|keep going|continue|"
    r"elaborate(?: on (?:that|this|it))?|expand(?: on (?:that|this|it))?|"
    r"explain(?: (?:that|this|it)(?: (?:in )?more detail)?)?|"
    r"why(?: is that)?|how so|what do you mean(?: by that)?|"
    r"(?:can|could|would) you (?:explain|clarify|elaborate|expand|give|show|provide)"
    r"(?: (?:me )?(?:an? )?(?:example|one|that|this|it|more detail))?|"
    r"(?:give|show) (?:me )?(?:an? )?(?:example|one)|"
    r"(?:and )?(?:the )?(?:first|second|third|fourth|fifth|last|next|other) one|"
    r"what else|anything else|and|so|then what"
    r")(?: (?:in )?more detail| please| then)?$",
    re.IGNORECASE,
)
_REFUSAL_RE = re.compile(
    r"(?:could ?n[o']?t|cannot|can ?n[o']?t|unable to)\s+(?:find|locate|answer)"
    r"|(?:does not|doesn't|do not|don't)\s+(?:contain|cover|include|mention)"
    r"|not (?:covered|included|available|specified) (?:in|by) the (?:reyvo )?knowledge base"
    r"|no (?:information|details) (?:about|on|for) (?:that|this)",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "might",
        "not",
        "of",
        "on",
        "or",
        "over",
        "should",
        "than",
        "that",
        "the",
        "their",
        "then",
        "these",
        "they",
        "this",
        "those",
        "to",
        "under",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "yes",
    }
)


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_slug: str
    document_title: str
    heading_path: str
    anchor: str
    content: str
    score: float
    lexical_score: float = 0.0
    vector_score: float = 0.0


@dataclass(slots=True)
class ChatExchange:
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    used_context: bool = False


@dataclass(slots=True)
class IngestResult:
    slug: str
    title: str
    version: str | None
    checksum: str
    chunk_count: int
    embedded_chunk_count: int
    skipped: bool = False


def tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOPWORDS]


def is_context_follow_up(question: str) -> bool:
    """Return ``True`` when a message only makes sense with the previous topic.

    Follow-ups such as "tell me more", "why?" or "how does that work?" carry
    little or no retrieval signal of their own. They are resolved against the
    most recent standalone question instead of being searched verbatim. Topic
    shifts that name their own subject ("what about campaigns?") are not
    follow-ups, so they search the new subject normally.
    """

    normalized = " ".join(re.sub(r"[^a-z0-9\s]", " ", question.lower()).split())
    if not normalized:
        return False
    if _FOLLOW_UP_RE.match(normalized):
        return True
    return len(tokenize(question)) <= 1 and bool(_DEICTIC_RE.search(normalized))


def _anchor_question(history: list[ChatMessage], *, fallback: str | None = None) -> str | None:
    """Return the most recent user question that stands on its own.

    Chained follow-ups ("tell me more" followed by "why?") would otherwise
    anchor on a question with no retrieval signal. Walking back past earlier
    follow-ups keeps the conversation on the original topic. The conversation
    title is used when long conversations push every standalone question out of
    the history window; if that is also a follow-up, there is nothing to
    resolve and the caller refuses.
    """

    questions = [message.content for message in history if message.role == "user"]
    for question in reversed(questions):
        if not is_context_follow_up(question):
            return question
    if fallback and not is_context_follow_up(fallback):
        return fallback
    return None


def term_frequency(term: str, counts: Counter[str]) -> int:
    """Count a query term in a document, allowing light morphological variants.

    Questions rarely reuse the document's exact word form ("unsubscribes" versus
    "unsubscribe"), so short terms match by prefix once they share at least
    ``MIN_PREFIX_LENGTH`` characters. Exact matches are still preferred.
    """

    exact = counts.get(term, 0)
    if exact:
        return exact
    if len(term) < MIN_PREFIX_LENGTH:
        return 0
    total = 0
    for candidate, count in counts.items():
        if len(candidate) < MIN_PREFIX_LENGTH:
            continue
        if candidate.startswith(term) or term.startswith(candidate):
            total += count
    return total


def bm25_scores(query_tokens: list[str], documents: list[list[str]]) -> list[float]:
    """Score each document against the query using BM25."""

    if not documents:
        return []
    total = len(documents)
    average_length = sum(len(document) for document in documents) / total or 1.0
    unique_terms = set(query_tokens)
    term_counts = [Counter(document) for document in documents]

    frequencies: dict[str, list[int]] = {}
    document_frequency: dict[str, int] = {}
    for term in unique_terms:
        per_document = [term_frequency(term, counts) for counts in term_counts]
        frequencies[term] = per_document
        document_frequency[term] = sum(1 for frequency in per_document if frequency)

    scores: list[float] = []
    for index, document in enumerate(documents):
        length = len(document)
        score = 0.0
        for term in unique_terms:
            frequency = frequencies[term][index]
            if not frequency:
                continue
            df = document_frequency[term]
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            denominator = frequency + BM25_K1 * (1 - BM25_B + BM25_B * length / average_length)
            score += idf * frequency * (BM25_K1 + 1) / denominator
        scores.append(score)
    return scores


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def reciprocal_rank_fusion(rankings: list[list[int]]) -> dict[int, float]:
    """Fuse ranked result lists with reciprocal rank fusion."""

    fused: dict[int, float] = {}
    for ranking in rankings:
        for position, index in enumerate(ranking, start=1):
            fused[index] = fused.get(index, 0.0) + 1.0 / (RRF_K + position)
    return fused


def _snippet(text: str, limit: int) -> str:
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _citation(result: RetrievedChunk, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "document_slug": result.document_slug,
        "document_title": result.document_title,
        "heading_path": result.heading_path,
        "anchor": result.anchor,
        "snippet": _snippet(result.content, 280),
        "score": round(result.score, 4),
        "lexical_score": round(result.lexical_score, 4),
        "vector_score": round(result.vector_score, 4),
    }


def cited_indexes(answer: str, available: int) -> set[int]:
    """Return the source numbers the answer actually references."""

    indexes: set[int] = set()
    for value in _CITATION_RE.findall(answer):
        index = int(value)
        if 1 <= index <= available:
            indexes.add(index)
    return indexes


def _looks_like_refusal(answer: str) -> bool:
    """Detect a model response that says the knowledge base cannot answer."""

    normalized = _WHITESPACE_RE.sub(" ", answer).strip().lower()
    if not normalized:
        return True
    if normalized.startswith(CHAT_REFUSAL_TEXT.lower()):
        return True
    return bool(_REFUSAL_RE.search(normalized))


def _clean_citation_markers(answer: str, available: int) -> str:
    """Drop source markers that do not map to a supplied source."""

    def replace(match: re.Match[str]) -> str:
        return match.group(0) if 1 <= int(match.group(1)) <= available else ""

    cleaned = _CITATION_RE.sub(replace, answer)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:!?])", r"\1", cleaned)
    return cleaned.strip()


def _grounded_answer(answer: str, available: int) -> tuple[str | None, set[int]]:
    """Validate a model answer against the supplied sources.

    Returns the cleaned answer and the source numbers it cites. The answer is
    ``None`` when it must be refused: the model declined, cited a source that
    was never supplied, or said it could not answer without citing anything.
    An answer with no citations at all is returned unchanged with an empty set
    so the caller can ask the model once to repair it.
    """

    stripped = answer.strip()
    indexes = cited_indexes(stripped, available)
    cited_something = bool(_CITATION_RE.search(stripped))
    if (
        stripped.lower().startswith(CHAT_REFUSAL_TEXT.lower())
        or (cited_something and not indexes)
        or (not indexes and _looks_like_refusal(stripped))
    ):
        return None, set()
    return _clean_citation_markers(stripped, available), indexes


async def search_chunks(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 6,
    document_slug: str | None = None,
) -> list[RetrievedChunk]:
    """Return the most relevant chunks using hybrid lexical/vector search."""

    limit = max(1, min(limit, MAX_TOP_K))
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    statement = (
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(KnowledgeDocument.status == "active")
        .order_by(KnowledgeChunk.document_id, KnowledgeChunk.ordinal)
    )
    if document_slug is not None:
        statement = statement.where(KnowledgeDocument.slug == document_slug)

    rows = list((await session.execute(statement)).all())
    if not rows:
        return []

    document_tokens = [
        tokenize(f"{document.title} {document.title} {chunk.heading_path} {chunk.content}")
        for chunk, document in rows
    ]
    lexical = bm25_scores(query_tokens, document_tokens)

    vector = [0.0] * len(rows)
    embedding_provider = get_embedding_provider()
    has_embeddings = any(chunk.embedding for chunk, _document in rows)
    if embedding_provider.available and has_embeddings:
        try:
            query_vector = (await embedding_provider.embed([query]))[0]
        except Exception:  # pragma: no cover - provider failure degrades to lexical
            logger.warning("Embedding query failed; falling back to lexical search.", exc_info=True)
        else:
            for index, (chunk, _document) in enumerate(rows):
                if chunk.embedding:
                    vector[index] = cosine_similarity(query_vector, chunk.embedding)

    ranked_limit = max(limit * 4, 20)
    lexical_ranking = [
        index
        for index in sorted(range(len(rows)), key=lambda i: (-lexical[i], i))
        if lexical[index] > 0
    ][:ranked_limit]
    vector_ranking = [
        index
        for index in sorted(range(len(rows)), key=lambda i: (-vector[i], i))
        if vector[index] >= MIN_VECTOR_SCORE
    ][:ranked_limit]

    if not lexical_ranking and not vector_ranking:
        # Every query term is ubiquitous (for example "Reyvo"). Fall back to
        # documents that contain the most query terms, in document order.
        unique_terms = set(query_tokens)
        counts = [Counter(tokens) for tokens in document_tokens]
        matched = [
            sum(1 for term in unique_terms if term_frequency(term, counter)) for counter in counts
        ]
        candidates = [index for index, hits in enumerate(matched) if hits]
        candidates.sort(key=lambda i: (-matched[i], i))
        lexical_ranking = candidates[:ranked_limit]

    fused = reciprocal_rank_fusion([lexical_ranking, vector_ranking])
    ordered = sorted(
        (index for index, score in fused.items() if score > 0),
        key=lambda index: (-fused[index], index),
    )

    results: list[RetrievedChunk] = []
    for index in ordered[:limit]:
        chunk, document = rows[index]
        results.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=document.id,
                document_slug=document.slug,
                document_title=document.title,
                heading_path=chunk.heading_path,
                anchor=chunk.anchor,
                content=chunk.content,
                score=fused[index],
                lexical_score=lexical[index],
                vector_score=vector[index],
            )
        )
    return results


def build_context(
    results: list[RetrievedChunk], *, max_chars: int = MAX_CONTEXT_CHARS
) -> tuple[str, list[dict[str, Any]]]:
    """Render retrieved chunks as numbered, citable sources."""

    blocks: list[str] = []
    citations: list[dict[str, Any]] = []
    used = 0
    for index, result in enumerate(results, start=1):
        block = (
            f"[{index}] {result.document_title} > {result.heading_path}\n{result.content.strip()}"
        )
        if blocks and used + len(block) + 5 > max_chars:
            break
        blocks.append(block)
        citations.append(_citation(result, index))
        used += len(block) + 5
    return "\n\n---\n\n".join(blocks), citations


def format_history(messages: list[ChatMessage]) -> str:
    lines: list[str] = []
    for message in messages[-MAX_HISTORY_MESSAGES:]:
        label = "User" if message.role == "user" else "Assistant"
        lines.append(f"{label}: {_snippet(message.content, 800)}")
    return "\n".join(lines)[-MAX_HISTORY_CHARS:]


async def answer_question(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    message: str,
    conversation_id: uuid.UUID | None = None,
    top_k: int = 6,
) -> ChatExchange:
    """Answer one question from the knowledge base and persist both turns."""

    question = message.strip()
    if not question:
        raise BadRequestError("The question cannot be empty.")

    history: list[ChatMessage] = []
    if conversation_id is not None:
        conversation = await session.scalar(
            select(ChatConversation).where(
                ChatConversation.id == conversation_id,
                ChatConversation.tenant_id == tenant_id,
                ChatConversation.user_id == user_id,
            )
        )
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        history = list(
            (
                await session.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.conversation_id == conversation.id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(MAX_HISTORY_MESSAGES)
                )
            ).all()
        )[::-1]
    else:
        conversation = ChatConversation(
            tenant_id=tenant_id,
            user_id=user_id,
            title=_snippet(question, 80),
        )
        session.add(conversation)
        await session.flush()

    started = time.perf_counter()
    if is_context_follow_up(question):
        # Context-only follow-ups ("tell me more", "why?", "how does that
        # work?") have little or no retrieval signal of their own. Resolve them
        # against the most recent standalone question so retrieval stays on the
        # same topic. A follow-up with no history has nothing to resolve, and
        # off-topic questions are deliberately not resolved this way; both must
        # stand on their own or be refused.
        results = []
        anchor = _anchor_question(history, fallback=conversation.title) if history else None
        if anchor:
            results = await search_chunks(session, anchor, limit=top_k)
    else:
        results = await search_chunks(session, question, limit=top_k)

    citations: list[dict[str, Any]] = []
    result = None
    used_context = False
    if results:
        context, retrieved_citations = build_context(results)
        provider = get_chat_provider()
        history_text = format_history(history)
        result = await provider.answer(
            question=question,
            context=context,
            history=history_text,
        )
        answer, valid_indexes = _grounded_answer(result.answer, len(retrieved_citations))
        if answer is not None and not valid_indexes:
            # The model answered without citing anything. Give it one repair
            # attempt before treating the answer as ungrounded.
            result = await provider.answer(
                question=_REPAIR_INSTRUCTION.format(question=question),
                context=context,
                history=history_text,
            )
            answer, valid_indexes = _grounded_answer(result.answer, len(retrieved_citations))

        if answer is None or not valid_indexes:
            # Refusals, invented citations and answers that still have no
            # citation after the repair attempt are not presented as
            # knowledge-base content.
            result.answer = REFUSAL_MESSAGE
        else:
            result.answer = answer
            citations = [
                citation for citation in retrieved_citations if citation["index"] in valid_indexes
            ]
            used_context = True

    latency_ms = int((time.perf_counter() - started) * 1000)
    timestamp = utcnow()
    user_message = ChatMessage(
        conversation_id=conversation.id,
        role="user",
        content=question,
        citations=[],
        created_at=timestamp,
    )
    assistant_message = ChatMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=result.answer if result is not None else REFUSAL_MESSAGE,
        citations=citations,
        model=result.model if result is not None else None,
        prompt_tokens=result.prompt_tokens if result is not None else None,
        completion_tokens=result.completion_tokens if result is not None else None,
        latency_ms=latency_ms,
        created_at=timestamp + timedelta(microseconds=1),
    )
    session.add_all([user_message, assistant_message])
    conversation.updated_at = timestamp + timedelta(microseconds=1)
    await session.commit()

    return ChatExchange(
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        answer=assistant_message.content,
        citations=citations,
        model=assistant_message.model,
        used_context=used_context,
    )


async def list_conversations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ChatConversation], int]:
    filters = (
        ChatConversation.tenant_id == tenant_id,
        ChatConversation.user_id == user_id,
        ChatConversation.archived.is_(False),
    )
    total = int(
        await session.scalar(select(func.count()).select_from(ChatConversation).where(*filters))
        or 0
    )
    rows = await session.scalars(
        select(ChatConversation)
        .where(*filters)
        .order_by(ChatConversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total


async def get_conversation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> ChatConversation | None:
    return await session.scalar(
        select(ChatConversation).where(
            ChatConversation.id == conversation_id,
            ChatConversation.tenant_id == tenant_id,
            ChatConversation.user_id == user_id,
        )
    )


async def list_messages(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    limit: int = MAX_CONVERSATION_MESSAGES,
) -> list[ChatMessage]:
    """Return up to ``limit`` of the most recent messages, oldest first."""

    rows = list(
        (
            await session.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(limit)
            )
        ).all()
    )
    rows.reverse()
    return rows


async def delete_conversation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> None:
    conversation = await get_conversation(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    # Delete messages explicitly: SQLite does not enforce ON DELETE CASCADE unless
    # foreign keys are enabled, and production PostgreSQL handles it either way.
    await session.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conversation.id))
    await session.delete(conversation)
    await session.commit()


async def purge_conversations_before(
    session: AsyncSession,
    cutoff: datetime,
) -> int:
    """Delete conversations whose last activity is older than ``cutoff``.

    Messages are removed explicitly because SQLite does not enforce
    ``ON DELETE CASCADE`` unless foreign keys are enabled. Returns the number of
    conversations deleted.
    """

    conversation_ids = select(ChatConversation.id).where(ChatConversation.updated_at < cutoff)
    await session.execute(
        delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversation_ids))
    )
    result = await session.execute(
        delete(ChatConversation).where(ChatConversation.updated_at < cutoff)
    )
    await session.commit()
    return int(result.rowcount or 0)


async def ingest_document(
    session: AsyncSession,
    *,
    path: str | Path,
    slug: str,
    title: str | None = None,
    version: str | None = None,
    force: bool = False,
) -> IngestResult:
    """Parse a Word document and upsert it and its chunks.

    Re-ingestion is idempotent: an unchanged checksum is skipped unless
    ``force`` is set or embeddings are configured but the stored chunks do not
    have them yet (for example, the document was first ingested without a key).
    """

    source = Path(path)
    if not await asyncio.to_thread(source.exists):
        raise NotFoundError(f"Knowledge document not found: {source}")

    checksum = await asyncio.to_thread(_file_checksum, source)
    document = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.slug == slug))
    embedding_provider = get_embedding_provider()
    current_embedding_model = get_settings().ai_embedding_model.strip() or None
    stored_embedding_model = (
        (document.doc_metadata or {}).get("embedding_model") if document else None
    )
    embeddings_missing = (
        embedding_provider.available
        and document is not None
        and (
            document.embedded_chunk_count < document.chunk_count
            or stored_embedding_model != current_embedding_model
        )
    )
    if (
        document is not None
        and not force
        and not embeddings_missing
        and document.checksum == checksum
        and document.parser_version == PARSER_VERSION
    ):
        return IngestResult(
            slug=document.slug,
            title=document.title,
            version=document.version,
            checksum=document.checksum,
            chunk_count=document.chunk_count,
            embedded_chunk_count=document.embedded_chunk_count,
            skipped=True,
        )

    parsed = await asyncio.to_thread(parse_docx, source)
    sections: list[ParsedSection] = [section for section in parsed.sections if section.text.strip()]
    if not sections:
        raise BadRequestError("The document did not contain any readable text.")

    contents = [section.text.strip() for section in sections]
    embeddings: list[list[float] | None] = [None] * len(contents)
    accepted_embedding_model: str | None = None
    accepted_embedding_dim: int | None = None
    if embedding_provider.available:
        try:
            vectors = await embedding_provider.embed(contents)
        except Exception:  # embeddings are optional; keep lexical search usable
            logger.warning(
                "Embedding ingestion failed; storing the document without embeddings.",
                exc_info=True,
            )
        else:
            if len(vectors) == len(contents):
                embeddings = list(vectors)
                accepted_embedding_model = current_embedding_model
                accepted_embedding_dim = len(vectors[0]) if vectors else None

    if document is None:
        document = KnowledgeDocument(
            slug=slug,
            title=title or parsed.title,
            checksum=checksum,
            parser_version=PARSER_VERSION,
        )
        session.add(document)
        await session.flush()
    else:
        await session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
        )

    document.title = title or parsed.title
    document.version = version or parsed.version
    document.source_path = str(source)
    document.checksum = checksum
    document.parser_version = PARSER_VERSION
    document.status = "active"
    document.doc_metadata = {
        "block_count": parsed.block_count,
        "section_count": len(sections),
        "embedding_model": accepted_embedding_model,
        "embedding_dim": accepted_embedding_dim,
    }
    document.chunk_count = len(sections)
    document.embedded_chunk_count = sum(1 for embedding in embeddings if embedding)
    document.ingested_at = utcnow()

    for ordinal, (section, embedding) in enumerate(zip(sections, embeddings, strict=False)):
        session.add(
            KnowledgeChunk(
                document_id=document.id,
                ordinal=ordinal,
                heading_path=section.heading_path,
                anchor=section.anchor,
                content=section.text.strip(),
                token_count=max(1, len(section.text) // 4),
                embedding=embedding,
                chunk_metadata={
                    "heading_path": section.heading_path,
                    "anchor": section.anchor,
                    "parser_version": PARSER_VERSION,
                },
            )
        )

    try:
        await session.commit()
    except IntegrityError as exc:
        # Two reindexes can race between the checksum read and the insert.
        await session.rollback()
        raise ConflictError(
            "The knowledge document was updated concurrently. Retry the ingestion."
        ) from exc
    return IngestResult(
        slug=document.slug,
        title=document.title,
        version=document.version,
        checksum=document.checksum,
        chunk_count=document.chunk_count,
        embedded_chunk_count=document.embedded_chunk_count,
        skipped=False,
    )


async def ingest_design_document(
    session: AsyncSession,
    *,
    path: str | Path | None = None,
    force: bool = False,
) -> IngestResult:
    settings = get_settings()
    source = Path(path or settings.design_doc_path)
    if not source.is_absolute():
        source = Path.cwd() / source
    return await ingest_document(
        session,
        path=source,
        slug=DESIGN_DOC_SLUG,
        title=DESIGN_DOC_TITLE,
        force=force,
    )


async def knowledge_status(
    session: AsyncSession,
) -> tuple[list[KnowledgeDocument], int, int]:
    documents = list(
        (await session.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.title))).all()
    )
    chunk_count = int(await session.scalar(select(func.count()).select_from(KnowledgeChunk)) or 0)
    embedded_count = int(
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.embedding.is_not(None))
        )
        or 0
    )
    return documents, chunk_count, embedded_count


def citation_for(result: RetrievedChunk, index: int) -> dict[str, Any]:
    return _citation(result, index)


def _file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
