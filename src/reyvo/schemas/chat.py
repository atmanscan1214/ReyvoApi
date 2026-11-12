from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from reyvo.schemas.common import ORMModel


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: uuid.UUID | None = None
    top_k: int | None = Field(default=None, ge=1, le=12)


class Citation(BaseModel):
    index: int
    document_slug: str
    document_title: str
    heading_path: str
    anchor: str
    snippet: str
    score: float
    lexical_score: float = 0.0
    vector_score: float = 0.0


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    answer: str
    citations: list[Citation]
    model: str | None = None
    used_context: bool


class ConversationRead(ORMModel):
    id: uuid.UUID
    title: str
    archived: bool
    created_at: datetime
    updated_at: datetime


class ChatMessageRead(ORMModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list[dict[str, Any]]
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    created_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[ChatMessageRead] = Field(default_factory=list)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int | None = Field(default=None, ge=1, le=12)


class KnowledgeSearchResponse(BaseModel):
    query: str
    results: list[Citation]


class KnowledgeDocumentRead(ORMModel):
    id: uuid.UUID
    slug: str
    title: str
    version: str | None = None
    checksum: str
    status: str
    chunk_count: int
    embedded_chunk_count: int
    ingested_at: datetime | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None
    embeddings_stale: bool = False


class KnowledgeStatus(BaseModel):
    documents: list[KnowledgeDocumentRead]
    chunk_count: int
    embedded_chunk_count: int


class KnowledgeReindexRequest(BaseModel):
    force: bool = False
    background: bool = False


class KnowledgeReindexQueued(BaseModel):
    status: str = "queued"
    job_id: str


class KnowledgeReindexResponse(BaseModel):
    slug: str
    title: str
    version: str | None = None
    checksum: str
    chunk_count: int
    embedded_chunk_count: int
    skipped: bool
