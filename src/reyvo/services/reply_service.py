from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.exceptions import BadRequestError, NotFoundError
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.core.utils import utcnow
from reyvo.db.enums import ReplyCategory, TaskStatus
from reyvo.db.models import Contact, Membership, Reply, Task
from reyvo.schemas.reply import TaskCreate, TaskUpdate
from reyvo.services import audit_service


async def list_replies(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pagination: Pagination,
    *,
    category: ReplyCategory | None = None,
    needs_review: bool | None = None,
    campaign_id: uuid.UUID | None = None,
) -> tuple[list[Reply], int]:
    stmt = select(Reply).where(Reply.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(Reply).where(Reply.tenant_id == tenant_id)
    if category is not None:
        stmt = stmt.where(Reply.category == category)
        count_stmt = count_stmt.where(Reply.category == category)
    if needs_review is not None:
        stmt = stmt.where(Reply.needs_review == needs_review)
        count_stmt = count_stmt.where(Reply.needs_review == needs_review)
    if campaign_id is not None:
        stmt = stmt.where(Reply.campaign_id == campaign_id)
        count_stmt = count_stmt.where(Reply.campaign_id == campaign_id)

    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(Reply.created_at.desc())
        .limit(pagination.capped_limit)
        .offset(pagination.offset)
    )
    return list((await session.scalars(stmt)).all()), total


async def get_reply_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, reply_id: uuid.UUID
) -> Reply:
    reply = await session.scalar(
        select(Reply).where(Reply.id == reply_id, Reply.tenant_id == tenant_id)
    )
    if reply is None:
        raise NotFoundError("Reply not found.")
    return reply


async def update_reply(
    session: AsyncSession,
    principal: Principal,
    reply_id: uuid.UUID,
    *,
    category: ReplyCategory | None = None,
    needs_review: bool | None = None,
    assigned_to: uuid.UUID | None = None,
) -> Reply:
    reply = await get_reply_or_404(session, principal.tenant_id, reply_id)
    if assigned_to is not None:
        await _ensure_member(session, principal.tenant_id, assigned_to)
        reply.assigned_to = assigned_to
    if category is not None:
        reply.category = category
        reply.classified_by = "user"
        reply.needs_review = False
    if needs_review is not None:
        reply.needs_review = needs_review

    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="reply.updated",
        actor_id=principal.user_id,
        entity_type="reply",
        entity_id=reply.id,
        after_data={
            "category": category.value if category else None,
            "needs_review": needs_review,
            "assigned_to": str(assigned_to) if assigned_to else None,
        },
    )
    await session.commit()
    await session.refresh(reply)
    return reply


async def _ensure_member(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    membership = await session.scalar(
        select(Membership.id).where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == user_id,
        )
    )
    if membership is None:
        raise BadRequestError("The selected user is not a member of this workspace.")


async def create_task(session: AsyncSession, principal: Principal, payload: TaskCreate) -> Task:
    if payload.assignee_id is not None:
        await _ensure_member(session, principal.tenant_id, payload.assignee_id)
    if payload.contact_id is not None:
        contact = await session.scalar(
            select(Contact.id).where(
                Contact.id == payload.contact_id,
                Contact.tenant_id == principal.tenant_id,
            )
        )
        if contact is None:
            raise BadRequestError("The selected contact does not exist in this workspace.")
    if payload.opportunity_id is not None:
        from reyvo.db.models import Opportunity

        opportunity = await session.scalar(
            select(Opportunity.id).where(
                Opportunity.id == payload.opportunity_id,
                Opportunity.tenant_id == principal.tenant_id,
            )
        )
        if opportunity is None:
            raise BadRequestError("The selected opportunity does not exist in this workspace.")

    task = Task(
        tenant_id=principal.tenant_id,
        title=payload.title.strip(),
        due_at=payload.due_at,
        priority=payload.priority,
        contact_id=payload.contact_id,
        opportunity_id=payload.opportunity_id,
        assignee_id=payload.assignee_id,
        created_by=principal.user_id,
        status=TaskStatus.OPEN,
    )
    session.add(task)
    await session.flush()
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="task.created",
        actor_id=principal.user_id,
        entity_type="task",
        entity_id=task.id,
        after_data={"title": task.title},
    )
    await session.commit()
    await session.refresh(task)
    return task


async def list_tasks(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pagination: Pagination,
    *,
    status: TaskStatus | None = None,
    assignee_id: uuid.UUID | None = None,
) -> tuple[list[Task], int]:
    stmt = select(Task).where(Task.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(Task).where(Task.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
        count_stmt = count_stmt.where(Task.status == status)
    if assignee_id is not None:
        stmt = stmt.where(Task.assignee_id == assignee_id)
        count_stmt = count_stmt.where(Task.assignee_id == assignee_id)

    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(Task.due_at.is_(None), Task.due_at)
        .limit(pagination.capped_limit)
        .offset(pagination.offset)
    )
    return list((await session.scalars(stmt)).all()), total


async def update_task(
    session: AsyncSession, principal: Principal, task_id: uuid.UUID, payload: TaskUpdate
) -> Task:
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.tenant_id == principal.tenant_id)
    )
    if task is None:
        raise NotFoundError("Task not found.")

    data = payload.model_dump(exclude_unset=True)
    if data.get("assignee_id") is not None:
        await _ensure_member(session, principal.tenant_id, data["assignee_id"])
    for field, value in data.items():
        setattr(task, field, value)
    if task.status == TaskStatus.COMPLETED and task.completed_at is None:
        task.completed_at = utcnow()

    await session.commit()
    await session.refresh(task)
    return task
