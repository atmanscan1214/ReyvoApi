from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_pagination, get_principal
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.enums import TaskStatus
from reyvo.db.session import get_db
from reyvo.schemas.common import Page
from reyvo.schemas.reply import TaskCreate, TaskRead, TaskUpdate
from reyvo.services import reply_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=Page[TaskRead])
async def list_tasks(
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    assignee_id: uuid.UUID | None = Query(default=None),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Page[TaskRead]:
    items, total = await reply_service.list_tasks(
        session,
        principal.tenant_id,
        pagination,
        status=status_filter,
        assignee_id=assignee_id,
    )
    return Page[TaskRead](
        items=[TaskRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> TaskRead:
    task = await reply_service.create_task(session, principal, payload)
    return TaskRead.model_validate(task)


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> TaskRead:
    task = await reply_service.update_task(session, principal, task_id, payload)
    return TaskRead.model_validate(task)
