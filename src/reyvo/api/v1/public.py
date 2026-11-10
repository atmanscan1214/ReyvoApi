from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.db.session import get_db
from reyvo.schemas.suppression import (
    UnsubscribeInfo,
    UnsubscribeRequest,
    UnsubscribeResponse,
)
from reyvo.services import suppression_service

router = APIRouter(prefix="/u", tags=["public"])


@router.get("/{token}", response_model=UnsubscribeInfo)
async def get_unsubscribe_page(
    token: str,
    session: AsyncSession = Depends(get_db),
) -> UnsubscribeInfo:
    return await suppression_service.get_unsubscribe_info(session, token)


@router.post("/{token}", response_model=UnsubscribeResponse)
async def unsubscribe(
    token: str,
    payload: UnsubscribeRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_db),
) -> UnsubscribeResponse:
    return await suppression_service.unsubscribe(
        session, token, reason=payload.reason if payload else None
    )
