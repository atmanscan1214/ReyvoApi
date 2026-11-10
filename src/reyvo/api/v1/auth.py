from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_principal
from reyvo.core.exceptions import BadRequestError
from reyvo.core.security import decode_sso_state
from reyvo.core.tenancy import Principal
from reyvo.db.session import get_db
from reyvo.schemas.auth import (
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    TenantSummary,
    TokenPair,
    UserRead,
)
from reyvo.schemas.sso import SSOAuthorizeResponse, SSOCallbackRequest
from reyvo.services import auth_service, sso_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenPair:
    principal = await auth_service.register(session, payload)
    return auth_service.build_token_pair(principal)


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenPair:
    principal = await auth_service.authenticate(session, payload)
    return auth_service.build_token_pair(principal)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenPair:
    principal = await auth_service.refresh(session, refresh_token=payload.refresh_token)
    return auth_service.build_token_pair(principal)


@router.get("/me", response_model=MeResponse)
async def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    return MeResponse(
        user=UserRead.model_validate(principal.user),
        tenant=TenantSummary.model_validate(principal.tenant),
        role=principal.role,
    )


@router.get("/sso/authorize", response_model=SSOAuthorizeResponse)
async def sso_authorize(
    tenant_slug: str = Query(min_length=1, max_length=80),
    login_hint: str | None = Query(default=None, max_length=320),
    domain_hint: str | None = Query(default=None, max_length=255),
    return_to: str | None = Query(default=None, max_length=2048),
    session: AsyncSession = Depends(get_db),
) -> SSOAuthorizeResponse:
    return await sso_service.authorize(
        session,
        tenant_slug=tenant_slug,
        login_hint=login_hint,
        domain_hint=domain_hint,
        return_to=return_to,
    )


@router.get("/sso/callback", response_model=None)
async def sso_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> TokenPair | RedirectResponse:
    if error:
        raise BadRequestError(
            error_description or "The identity provider rejected the sign-in.",
            details={"workos_error": error},
        )
    if not code or not state:
        raise BadRequestError("The SSO callback is missing the code or state.")

    principal = await sso_service.complete_login(session, code=code, state=state)
    token_pair = auth_service.build_token_pair(principal)

    return_to = decode_sso_state(state).get("return_to")
    if not return_to:
        return token_pair

    fragment = urlencode(
        {
            "access_token": token_pair.access_token,
            "refresh_token": token_pair.refresh_token,
            "token_type": token_pair.token_type,
            "expires_in": token_pair.expires_in,
            "tenant": token_pair.tenant.slug,
        }
    )
    return RedirectResponse(f"{return_to}#{fragment}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sso/callback", response_model=TokenPair)
async def sso_callback_post(
    payload: SSOCallbackRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenPair:
    principal = await sso_service.complete_login(session, code=payload.code, state=payload.state)
    return auth_service.build_token_pair(principal)
