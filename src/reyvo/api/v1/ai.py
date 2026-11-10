from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_principal
from reyvo.core.tenancy import Principal
from reyvo.db.session import get_db
from reyvo.schemas.ai import DraftStepRequest, DraftStepResponse
from reyvo.services import ai_service

router = APIRouter(prefix="/ai", tags=["ai"])


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
    )
