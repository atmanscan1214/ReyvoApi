from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_pagination, get_principal
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.enums import ReplyCategory
from reyvo.db.session import get_db
from reyvo.schemas.common import Page
from reyvo.schemas.reply import ReplyRead, ReplyUpdate
from reyvo.services import reply_service

router = APIRouter(prefix="/replies", tags=["replies"])


@router.get("", response_model=Page[ReplyRead])
async def list_replies(
    category: ReplyCategory | None = Query(default=None),
    needs_review: bool | None = Query(default=None),
    campaign_id: uuid.UUID | None = Query(default=None),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Page[ReplyRead]:
    items, total = await reply_service.list_replies(
        session,
        principal.tenant_id,
        pagination,
        category=category,
        needs_review=needs_review,
        campaign_id=campaign_id,
    )
    return Page[ReplyRead](
        items=[ReplyRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@router.patch("/{reply_id}", response_model=ReplyRead)
async def update_reply(
    reply_id: uuid.UUID,
    payload: ReplyUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ReplyRead:
    reply = await reply_service.update_reply(
        session,
        principal,
        reply_id,
        category=payload.category,
        needs_review=payload.needs_review,
        assigned_to=payload.assigned_to,
    )
    return ReplyRead.model_validate(reply)
