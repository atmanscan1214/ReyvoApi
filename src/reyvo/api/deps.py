from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.exceptions import ForbiddenError, UnauthorizedError
from reyvo.core.pagination import Pagination
from reyvo.core.request_context import get_request_id
from reyvo.core.tenancy import Principal, set_tenant_context
from reyvo.db.enums import MemberRole
from reyvo.db.session import get_db
from reyvo.services import auth_service

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Authentication is required.")
    principal = await auth_service.principal_from_access_token(session, credentials.credentials)
    await set_tenant_context(session, principal.tenant_id)
    return principal


def require_roles(*roles: MemberRole) -> Callable[..., Any]:
    allowed = {role.value for role in roles}

    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in allowed:
            raise ForbiddenError(
                "Your role does not allow this action.",
                details={"required_roles": sorted(allowed)},
            )
        return principal

    return dependency


async def require_platform_admin(
    principal: Principal = Depends(get_principal),
) -> Principal:
    if not principal.is_platform_admin:
        raise ForbiddenError("Platform administrator access is required.")
    return principal


def get_pagination(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


def client_meta(request: Request) -> dict[str, str | None]:
    forwarded = request.headers.get("x-forwarded-for")
    ip_address = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    return {
        "ip_address": ip_address,
        "user_agent": request.headers.get("user-agent"),
        "request_id": get_request_id(),
    }


CurrentPrincipal = Depends(get_principal)
PaginationDep = Depends(get_pagination)
DatabaseDep = Depends(get_db)
