from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_principal
from reyvo.core.tenancy import Principal
from reyvo.db.enums import EmailProvider
from reyvo.db.session import get_db
from reyvo.schemas.email_account import (
    EmailAccountRead,
    EmailAccountUpdate,
    OAuthCallbackResponse,
    OAuthStartResponse,
)
from reyvo.services import email_account_service

router = APIRouter(prefix="/email-accounts", tags=["email-accounts"])


@router.get("", response_model=list[EmailAccountRead])
async def list_email_accounts(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[EmailAccountRead]:
    accounts = await email_account_service.list_email_accounts(session, principal.tenant_id)
    return [EmailAccountRead.model_validate(account) for account in accounts]


@router.post("/oauth/{provider}/start", response_model=OAuthStartResponse)
async def start_oauth(
    provider: EmailProvider,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> OAuthStartResponse:
    return await email_account_service.start_oauth(session, principal, provider)


@router.get("/oauth/{provider}/callback", response_model=OAuthCallbackResponse)
async def oauth_callback(
    provider: EmailProvider,
    code: str = Query(min_length=1),
    state: str = Query(min_length=1),
    session: AsyncSession = Depends(get_db),
) -> OAuthCallbackResponse:
    account = await email_account_service.complete_oauth(
        session, provider=provider, code=code, state=state
    )
    return OAuthCallbackResponse(
        account=EmailAccountRead.model_validate(account),
        message="Sending inbox connected.",
    )


@router.patch("/{account_id}", response_model=EmailAccountRead)
async def update_email_account(
    account_id: uuid.UUID,
    payload: EmailAccountUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> EmailAccountRead:
    account = await email_account_service.update_email_account(
        session, principal, account_id, payload
    )
    return EmailAccountRead.model_validate(account)


@router.delete("/{account_id}", response_model=EmailAccountRead)
async def disconnect_email_account(
    account_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> EmailAccountRead:
    account = await email_account_service.disconnect_email_account(session, principal, account_id)
    return EmailAccountRead.model_validate(account)


@router.post("/{account_id}/test", response_model=dict, status_code=status.HTTP_200_OK)
async def test_email_account(
    account_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    await email_account_service.get_email_account_or_404(session, principal.tenant_id, account_id)
    return {
        "ok": True,
        "message": "The inbox is registered. Live provider checks run in the sender worker.",
    }
