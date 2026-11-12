from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import (
    enforce_ai_rate_limit,
    get_pagination,
    get_principal,
    require_knowledge_access,
    require_platform_admin,
)
from reyvo.core.config import get_settings
from reyvo.core.exceptions import NotFoundError
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.models import KnowledgeDocument
from reyvo.db.session import get_db
from reyvo.schemas.ai import DraftStepRequest, DraftStepResponse
from reyvo.schemas.chat import (
    ChatMessageRead,
    ChatRequest,
    ChatResponse,
    Citation,
    ConversationDetail,
    ConversationRead,
    KnowledgeDocumentRead,
    KnowledgeReindexQueued,
    KnowledgeReindexRequest,
    KnowledgeReindexResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeStatus,
)
from reyvo.schemas.common import Page
from reyvo.services import ai_service, knowledge_service
from reyvo.workers.queue import enqueue_job

router = APIRouter(prefix="/ai", tags=["ai"])
logger = logging.getLogger(__name__)


@router.post("/draft", response_model=DraftStepResponse)
async def draft_step(
    payload: DraftStepRequest,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> DraftStepResponse:
    result = await ai_service.draft_campaign_step(session, principal, payload)
    return DraftStepResponse(
        subject=result.subject,
        body_html=result.body_html,
        body_text=result.body_text,
        model=result.model,
        prompt_version=result.prompt_version,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(enforce_ai_rate_limit),
) -> ChatResponse:
    exchange = await knowledge_service.answer_question(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        message=payload.message,
        conversation_id=payload.conversation_id,
        top_k=payload.top_k or get_settings().knowledge_top_k,
    )
    return ChatResponse(
        conversation_id=exchange.conversation_id,
        user_message_id=exchange.user_message_id,
        assistant_message_id=exchange.assistant_message_id,
        answer=exchange.answer,
        citations=[Citation(**citation) for citation in exchange.citations],
        model=exchange.model,
        used_context=exchange.used_context,
    )


@router.get("/conversations", response_model=Page[ConversationRead])
async def list_conversations(
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_knowledge_access),
) -> Page[ConversationRead]:
    rows, total = await knowledge_service.list_conversations(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )
    return Page[ConversationRead](
        items=[ConversationRead.model_validate(row) for row in rows],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    message_limit: int = Query(
        default=200,
        ge=1,
        le=500,
        description="Maximum number of most recent messages to return.",
    ),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_knowledge_access),
) -> ConversationDetail:
    conversation = await knowledge_service.get_conversation(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    messages = await knowledge_service.list_messages(session, conversation.id, limit=message_limit)
    return ConversationDetail(
        **ConversationRead.model_validate(conversation).model_dump(),
        messages=[ChatMessageRead.model_validate(message) for message in messages],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_knowledge_access),
) -> None:
    await knowledge_service.delete_conversation(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        conversation_id=conversation_id,
    )


@router.post("/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(enforce_ai_rate_limit),
) -> KnowledgeSearchResponse:
    results = await knowledge_service.search_chunks(
        session, payload.query, limit=payload.limit or get_settings().knowledge_top_k
    )
    return KnowledgeSearchResponse(
        query=payload.query,
        results=[
            Citation(**knowledge_service.citation_for(result, index))
            for index, result in enumerate(results, start=1)
        ],
    )


@router.get("/knowledge/status", response_model=KnowledgeStatus)
async def knowledge_status(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_knowledge_access),
) -> KnowledgeStatus:
    documents, chunk_count, embedded_count = await knowledge_service.knowledge_status(session)
    current_model = get_settings().ai_embedding_model.strip() or None
    return KnowledgeStatus(
        documents=[_document_read(document, current_model) for document in documents],
        chunk_count=chunk_count,
        embedded_chunk_count=embedded_count,
    )


def _document_read(document: KnowledgeDocument, current_model: str | None) -> KnowledgeDocumentRead:
    """Attach embedding metadata (stored in ``doc_metadata``) to the response."""

    metadata = document.doc_metadata or {}
    status = KnowledgeDocumentRead.model_validate(document)
    status.embedding_model = metadata.get("embedding_model")
    status.embedding_dim = metadata.get("embedding_dim")
    status.embeddings_stale = bool(
        document.embedded_chunk_count and status.embedding_model != current_model
    )
    return status


@router.post(
    "/knowledge/reindex",
    response_model=KnowledgeReindexResponse | KnowledgeReindexQueued,
)
async def reindex_knowledge(
    payload: KnowledgeReindexRequest | None = None,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform_admin),
) -> KnowledgeReindexResponse | KnowledgeReindexQueued:
    request = payload or KnowledgeReindexRequest()
    if request.background:
        job_id = await enqueue_job("reindex_design_document", force=request.force)
        if job_id is not None:
            return KnowledgeReindexQueued(job_id=job_id)
        logger.warning("Job queue unavailable; running knowledge reindex inline.")

    result = await knowledge_service.ingest_design_document(session, force=request.force)
    return KnowledgeReindexResponse(
        slug=result.slug,
        title=result.title,
        version=result.version,
        checksum=result.checksum,
        chunk_count=result.chunk_count,
        embedded_chunk_count=result.embedded_chunk_count,
        skipped=result.skipped,
    )
