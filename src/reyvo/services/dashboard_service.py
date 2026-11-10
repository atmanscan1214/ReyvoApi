from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.utils import utcnow
from reyvo.db.enums import (
    CampaignStatus,
    ContactStatus,
    MessageDirection,
    MessageStatus,
    OpportunityStage,
    ReplyCategory,
    TaskStatus,
)
from reyvo.db.models import (
    Campaign,
    Company,
    Contact,
    EmailAccount,
    Message,
    Opportunity,
    Reply,
    Task,
)
from reyvo.schemas.dashboard import CampaignPerformance, DashboardSummary
from reyvo.services.opportunity_service import pipeline_summary


async def summary(session: AsyncSession, tenant_id: uuid.UUID) -> DashboardSummary:
    now = utcnow()
    since_7d = now - timedelta(days=7)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async def scalar(query) -> int:
        return int(await session.scalar(query) or 0)

    contacts_total = await scalar(
        select(func.count()).select_from(Contact).where(Contact.tenant_id == tenant_id)
    )
    contacts_new = await scalar(
        select(func.count())
        .select_from(Contact)
        .where(Contact.tenant_id == tenant_id, Contact.status == ContactStatus.NEW)
    )
    companies_total = await scalar(
        select(func.count()).select_from(Company).where(Company.tenant_id == tenant_id)
    )
    campaigns_total = await scalar(
        select(func.count()).select_from(Campaign).where(Campaign.tenant_id == tenant_id)
    )
    campaigns_active = await scalar(
        select(func.count())
        .select_from(Campaign)
        .where(Campaign.tenant_id == tenant_id, Campaign.status == CampaignStatus.ACTIVE)
    )
    messages_sent_total = await scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.tenant_id == tenant_id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.status == MessageStatus.SENT,
        )
    )
    messages_sent_7d = await scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.tenant_id == tenant_id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.status == MessageStatus.SENT,
            Message.sent_at >= since_7d,
        )
    )
    sent_today = await scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.tenant_id == tenant_id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.status == MessageStatus.SENT,
            Message.sent_at >= start_of_day,
        )
    )
    replies_total = await scalar(
        select(func.count()).select_from(Reply).where(Reply.tenant_id == tenant_id)
    )
    replies_7d = await scalar(
        select(func.count())
        .select_from(Reply)
        .where(Reply.tenant_id == tenant_id, Reply.created_at >= since_7d)
    )
    positive_replies_7d = await scalar(
        select(func.count())
        .select_from(Reply)
        .where(
            Reply.tenant_id == tenant_id,
            Reply.created_at >= since_7d,
            Reply.category == ReplyCategory.POSITIVE,
        )
    )
    meetings = await scalar(
        select(func.count())
        .select_from(Opportunity)
        .where(Opportunity.tenant_id == tenant_id, Opportunity.stage == OpportunityStage.MEETING)
    )
    opportunities_open = await scalar(
        select(func.count())
        .select_from(Opportunity)
        .where(
            Opportunity.tenant_id == tenant_id,
            Opportunity.stage.notin_({OpportunityStage.WON, OpportunityStage.NOT_INTERESTED}),
        )
    )
    pipeline_value_cents = int(
        await session.scalar(
            select(func.coalesce(func.sum(Opportunity.value_cents), 0)).where(
                Opportunity.tenant_id == tenant_id,
                Opportunity.stage.notin_({OpportunityStage.WON, OpportunityStage.NOT_INTERESTED}),
            )
        )
        or 0
    )
    tasks_open = await scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.tenant_id == tenant_id, Task.status == TaskStatus.OPEN)
    )
    daily_send_cap = int(
        await session.scalar(
            select(func.coalesce(func.sum(EmailAccount.daily_cap), 0)).where(
                EmailAccount.tenant_id == tenant_id
            )
        )
        or 0
    )

    top_stmt = (
        select(
            Message.campaign_id,
            Campaign.name,
            Campaign.status,
            func.count(Message.id).label("sent"),
        )
        .join(Campaign, Campaign.id == Message.campaign_id)
        .where(
            Message.tenant_id == tenant_id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.campaign_id.is_not(None),
        )
        .group_by(Message.campaign_id, Campaign.name, Campaign.status)
        .order_by(func.count(Message.id).desc())
        .limit(5)
    )
    reply_counts = dict(
        (
            await session.execute(
                select(Reply.campaign_id, func.count())
                .where(Reply.tenant_id == tenant_id, Reply.campaign_id.is_not(None))
                .group_by(Reply.campaign_id)
            )
        ).all()
    )
    top_campaigns = [
        CampaignPerformance(
            campaign_id=str(row[0]),
            name=row[1],
            status=row[2].value if hasattr(row[2], "value") else str(row[2]),
            sent=int(row[3]),
            replies=int(reply_counts.get(row[0], 0)),
        )
        for row in (await session.execute(top_stmt)).all()
    ]

    return DashboardSummary(
        contacts_total=contacts_total,
        contacts_new=contacts_new,
        companies_total=companies_total,
        campaigns_total=campaigns_total,
        campaigns_active=campaigns_active,
        messages_sent_total=messages_sent_total,
        messages_sent_7d=messages_sent_7d,
        replies_total=replies_total,
        replies_7d=replies_7d,
        positive_replies_7d=positive_replies_7d,
        meetings=meetings,
        opportunities_open=opportunities_open,
        pipeline_value_cents=pipeline_value_cents,
        tasks_open=tasks_open,
        daily_send_cap=daily_send_cap,
        sent_today=sent_today,
        as_of=now.date(),
        pipeline=await pipeline_summary(session, tenant_id),
        top_campaigns=top_campaigns,
    )
