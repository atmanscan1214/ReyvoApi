from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_pagination, get_principal, require_roles
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.enums import MemberRole
from reyvo.db.session import get_db
from reyvo.schemas.common import Page
from reyvo.schemas.sso import SSOSettingsUpdate, SSOStatus
from reyvo.schemas.tenant import (
    AuditLogRead,
    MemberRead,
    MemberUpdate,
    TenantRead,
    TenantUpdate,
    UsageRead,
)
from reyvo.services import sso_service, tenant_service

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("/current", response_model=TenantRead)
async def get_current_tenant(principal: Principal = Depends(get_principal)) -> TenantRead:
    return TenantRead.model_validate(principal.tenant)


@router.patch("/current", response_model=TenantRead)
async def update_current_tenant(
    payload: TenantUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(MemberRole.OWNER, MemberRole.ADMIN)),
) -> TenantRead:
    tenant = await tenant_service.update_tenant(session, principal, payload)
    return TenantRead.model_validate(tenant)


@router.get("/current/members", response_model=list[MemberRead])
async def list_members(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[MemberRead]:
    return await tenant_service.list_members(session, principal.tenant_id)


@router.patch("/current/members/{membership_id}", response_model=MemberRead)
async def update_member(
    membership_id: uuid.UUID,
    payload: MemberUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(MemberRole.OWNER)),
) -> MemberRead:
    return await tenant_service.update_member_role(session, principal, membership_id, payload.role)


@router.get("/current/usage", response_model=UsageRead)
async def get_usage(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> UsageRead:
    return await tenant_service.usage(session, principal)


@router.get("/current/audit-logs", response_model=Page[AuditLogRead])
async def list_audit_logs(
    action: str | None = Query(default=None),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(MemberRole.OWNER, MemberRole.ADMIN)),
) -> Page[AuditLogRead]:
    entries, total = await tenant_service.list_audit_logs(
        session, principal, pagination, action=action
    )
    return Page[AuditLogRead](
        items=[AuditLogRead.model_validate(entry) for entry in entries],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@router.get("/current/sso", response_model=SSOStatus)
async def get_sso_status(
    principal: Principal = Depends(require_roles(MemberRole.OWNER, MemberRole.ADMIN)),
) -> SSOStatus:
    return sso_service.status_for_tenant(principal.tenant)


@router.patch("/current/sso", response_model=SSOStatus)
async def update_sso_settings(
    payload: SSOSettingsUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(MemberRole.OWNER, MemberRole.ADMIN)),
) -> SSOStatus:
    return await sso_service.update_settings(session, principal, payload)


@router.delete("/current/sso", response_model=SSOStatus)
async def disable_sso(
    reason: str | None = Query(default=None, max_length=255),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(MemberRole.OWNER, MemberRole.ADMIN)),
) -> SSOStatus:
    return await sso_service.disable_sso(session, principal, reason=reason)
