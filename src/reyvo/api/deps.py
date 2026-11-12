from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.config import get_settings
from reyvo.core.exceptions import ForbiddenError, RateLimitedError, UnauthorizedError
from reyvo.core.pagination import Pagination
from reyvo.core.rate_limit import rate_limiter
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


async def require_knowledge_access(
    principal: Principal = Depends(get_principal),
) -> Principal:
    """Gate the internal design-document assistant.

    The default ``platform_admin`` policy keeps commercial assumptions,
    estimates and security design out of tenant-facing accounts. Set
    ``KNOWLEDGE_CHAT_ACCESS=members`` once the corpus is curated for customers.
    """

    if get_settings().knowledge_chat_access == "members":
        return principal
    if not principal.is_platform_admin:
        raise ForbiddenError("The knowledge assistant is restricted to platform administrators.")
    return principal


_RATE_WINDOW_SECONDS = 60


async def enforce_ai_rate_limit(
    principal: Principal = Depends(require_knowledge_access),
) -> Principal:
    """Throttle per-user AI calls.

    Uses a shared Redis window when ``REDIS_URL`` is configured and falls back
    to an in-process window otherwise (or during a Redis outage).
    """

    limit = get_settings().ai_chat_rate_limit_per_minute
    if limit <= 0:
        return principal

    result = await rate_limiter.hit(
        f"ai-chat:{principal.tenant_id}:{principal.user_id}",
        limit=limit,
        window_seconds=_RATE_WINDOW_SECONDS,
    )
    if not result.allowed:
        raise RateLimitedError(
            "Too many AI requests. Try again shortly.",
            details={
                "retry_after_seconds": result.retry_after_seconds,
                "limit": result.limit,
                "window_seconds": _RATE_WINDOW_SECONDS,
            },
        )
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
