from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_pagination, require_platform_admin
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.enums import TenantStatus
from reyvo.db.models import AuditLog, Tenant
from reyvo.db.session import get_db
from reyvo.schemas.admin import AdminTenantRead, AdminTenantUpdate
from reyvo.schemas.common import Page
from reyvo.schemas.tenant import AuditLogRead
from reyvo.services import audit_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/tenants", response_model=Page[AdminTenantRead])
async def list_tenants(
    status_filter: TenantStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=255),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform_admin),
) -> Page[AdminTenantRead]:
    stmt = select(Tenant)
    count_stmt = select(func.count()).select_from(Tenant)
    if status_filter is not None:
        stmt = stmt.where(Tenant.status == status_filter)
        count_stmt = count_stmt.where(Tenant.status == status_filter)
    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Tenant.name).like(pattern) | func.lower(Tenant.slug).like(pattern)
        )
        count_stmt = count_stmt.where(
            func.lower(Tenant.name).like(pattern) | func.lower(Tenant.slug).like(pattern)
        )

    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(Tenant.created_at.desc())
        .limit(pagination.capped_limit)
        .offset(pagination.offset)
    )
    tenants = list((await session.scalars(stmt)).all())
    return Page[AdminTenantRead](
        items=[AdminTenantRead.model_validate(tenant) for tenant in tenants],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@router.patch("/tenants/{tenant_id}", response_model=AdminTenantRead)
async def update_tenant(
    tenant_id: uuid.UUID,
    payload: AdminTenantUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform_admin),
) -> AdminTenantRead:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        from reyvo.core.exceptions import NotFoundError

        raise NotFoundError("Tenant not found.")

    before = {"status": tenant.status.value, "plan_code": tenant.plan_code}
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(tenant, field, value)
    await audit_service.record(
        session,
        tenant_id=tenant.id,
        action="admin.tenant_updated",
        actor_id=principal.user_id,
        entity_type="tenant",
        entity_id=tenant.id,
        before_data=before,
        after_data={key: str(value) for key, value in data.items()},
    )
    await session.commit()
    await session.refresh(tenant)
    return AdminTenantRead.model_validate(tenant)


@router.get("/audit-logs", response_model=Page[AuditLogRead])
async def list_platform_audit_logs(
    tenant_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform_admin),
) -> Page[AuditLogRead]:
    stmt = select(AuditLog)
    count_stmt = select(func.count()).select_from(AuditLog)
    if tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
        count_stmt = count_stmt.where(AuditLog.tenant_id == tenant_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
        count_stmt = count_stmt.where(AuditLog.action == action)

    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(AuditLog.created_at.desc())
        .limit(pagination.capped_limit)
        .offset(pagination.offset)
    )
    entries = list((await session.scalars(stmt)).all())
    return Page[AuditLogRead](
        items=[AuditLogRead.model_validate(entry) for entry in entries],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )
