from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from reyvo.core.config import get_settings
from reyvo.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PreflightError,
)
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.core.utils import utcnow
from reyvo.db.enums import (
    ApprovalStatus,
    CampaignStatus,
    ContactStatus,
    EmailAccountStatus,
    EnrollmentStatus,
    MessageDirection,
)
from reyvo.db.models import (
    Campaign,
    CampaignEnrollment,
    CampaignStep,
    Contact,
    ContactList,
    ContactListMember,
    EmailAccount,
    Message,
    Reply,
    Segment,
    Tenant,
)
from reyvo.schemas.campaign import (
    CampaignCreate,
    CampaignPreviewResponse,
    CampaignStats,
    CampaignUpdate,
)
from reyvo.services import audit_service
from reyvo.services.outreach_service import compose_outreach_message
from reyvo.services.suppression_service import is_suppressed


async def get_campaign_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID
) -> Campaign:
    campaign = await session.scalar(
        select(Campaign)
        .options(selectinload(Campaign.steps))
        .where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id)
    )
    if campaign is None:
        raise NotFoundError("Campaign not found.")
    return campaign


async def list_campaigns(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pagination: Pagination,
    *,
    status: CampaignStatus | None = None,
    search: str | None = None,
) -> tuple[list[Campaign], int]:
    stmt = (
        select(Campaign)
        .options(selectinload(Campaign.steps))
        .where(Campaign.tenant_id == tenant_id)
    )
    count_stmt = select(func.count()).select_from(Campaign).where(Campaign.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(Campaign.status == status)
        count_stmt = count_stmt.where(Campaign.status == status)
    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(func.lower(Campaign.name).like(pattern))
        count_stmt = count_stmt.where(func.lower(Campaign.name).like(pattern))

    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(Campaign.created_at.desc())
        .limit(pagination.capped_limit)
        .offset(pagination.offset)
    )
    return list((await session.scalars(stmt)).all()), total


async def _validate_email_accounts(
    session: AsyncSession, tenant_id: uuid.UUID, account_ids: list[uuid.UUID]
) -> list[str]:
    if not account_ids:
        return []
    rows = (
        await session.scalars(
            select(EmailAccount).where(
                EmailAccount.tenant_id == tenant_id,
                EmailAccount.id.in_(account_ids),
            )
        )
    ).all()
    found = {account.id for account in rows}
    missing = [str(account_id) for account_id in account_ids if account_id not in found]
    if missing:
        raise BadRequestError(
            "One or more sending inboxes do not belong to this workspace.",
            details={"missing_email_account_ids": missing},
        )
    return [str(account.id) for account in rows]


async def _validate_audience(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    audience_type: str,
    audience_id: uuid.UUID | None,
    contact_ids: list[uuid.UUID],
) -> list[str]:
    if audience_type == "contacts":
        if not contact_ids:
            return []
        rows = (
            await session.scalars(
                select(Contact.id).where(
                    Contact.tenant_id == tenant_id,
                    Contact.id.in_(contact_ids),
                )
            )
        ).all()
        found = {contact_id for contact_id in rows}
        missing = [str(contact_id) for contact_id in contact_ids if contact_id not in found]
        if missing:
            raise BadRequestError(
                "One or more contacts do not belong to this workspace.",
                details={"missing_contact_ids": missing},
            )
        return [str(contact_id) for contact_id in dict.fromkeys(contact_ids)]

    if audience_id is None:
        raise BadRequestError("audience_id is required for list and segment audiences.")
    if audience_type == "list":
        exists = await session.scalar(
            select(ContactList.id).where(
                ContactList.id == audience_id,
                ContactList.tenant_id == tenant_id,
            )
        )
    else:
        exists = await session.scalar(
            select(Segment.id).where(
                Segment.id == audience_id,
                Segment.tenant_id == tenant_id,
            )
        )
    if exists is None:
        raise BadRequestError("The selected audience does not exist in this workspace.")
    return []


async def create_campaign(
    session: AsyncSession, principal: Principal, payload: CampaignCreate
) -> Campaign:
    step_orders = [step.step_order for step in payload.steps]
    if len(set(step_orders)) != len(step_orders):
        raise BadRequestError("Step order values must be unique.")

    audience_snapshot = await _validate_audience(
        session,
        principal.tenant_id,
        audience_type=payload.audience_type,
        audience_id=payload.audience_id,
        contact_ids=payload.contact_ids,
    )
    email_account_ids = await _validate_email_accounts(
        session, principal.tenant_id, payload.email_account_ids
    )

    campaign = Campaign(
        tenant_id=principal.tenant_id,
        name=payload.name.strip(),
        status=CampaignStatus.DRAFT,
        audience_type=payload.audience_type,
        audience_id=payload.audience_id,
        audience_snapshot=audience_snapshot,
        email_account_ids=email_account_ids,
        timezone=payload.timezone,
        sending_window=payload.sending_window,
        daily_cap=payload.daily_cap,
        stop_on_reply=payload.stop_on_reply,
        approval_status=ApprovalStatus.PENDING,
        created_by=principal.user_id,
    )
    for step in sorted(payload.steps, key=lambda item: item.step_order):
        campaign.steps.append(
            CampaignStep(
                tenant_id=principal.tenant_id,
                step_order=step.step_order,
                delay_business_days=step.delay_business_days,
                subject_template=step.subject_template,
                body_html_template=step.body_html_template,
                body_text_template=step.body_text_template,
                ai_generated=step.ai_generated,
                ai_prompt_version=step.ai_prompt_version,
                approval_status=ApprovalStatus.PENDING,
            )
        )
    session.add(campaign)
    await session.flush()
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="campaign.created",
        actor_id=principal.user_id,
        entity_type="campaign",
        entity_id=campaign.id,
        after_data={"name": campaign.name, "steps": len(campaign.steps)},
    )
    await session.commit()
    return await get_campaign_or_404(session, principal.tenant_id, campaign.id)


async def update_campaign(
    session: AsyncSession,
    principal: Principal,
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
) -> Campaign:
    campaign = await get_campaign_or_404(session, principal.tenant_id, campaign_id)
    if campaign.status == CampaignStatus.ACTIVE:
        raise BadRequestError("Pause the campaign before editing it.")

    data = payload.model_dump(exclude_unset=True)
    steps = data.pop("steps", None)
    if "email_account_ids" in data and data["email_account_ids"] is not None:
        data["email_account_ids"] = await _validate_email_accounts(
            session, principal.tenant_id, data["email_account_ids"]
        )
    for field, value in data.items():
        setattr(campaign, field, value)

    if steps is not None:
        step_orders = [step["step_order"] for step in steps]
        if len(set(step_orders)) != len(step_orders):
            raise BadRequestError("Step order values must be unique.")
        campaign.steps.clear()
        await session.flush()
        for step in sorted(steps, key=lambda item: item["step_order"]):
            campaign.steps.append(
                CampaignStep(
                    tenant_id=principal.tenant_id,
                    step_order=step["step_order"],
                    delay_business_days=step["delay_business_days"],
                    subject_template=step["subject_template"],
                    body_html_template=step["body_html_template"],
                    body_text_template=step.get("body_text_template"),
                    ai_generated=step.get("ai_generated", False),
                    ai_prompt_version=step.get("ai_prompt_version"),
                    approval_status=ApprovalStatus.PENDING,
                )
            )
        campaign.approval_status = ApprovalStatus.PENDING
        if campaign.status == CampaignStatus.APPROVED:
            campaign.status = CampaignStatus.PENDING_APPROVAL

    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="campaign.updated",
        actor_id=principal.user_id,
        entity_type="campaign",
        entity_id=campaign.id,
        after_data={"fields": sorted(data.keys()), "steps_replaced": steps is not None},
    )
    await session.commit()
    return await get_campaign_or_404(session, principal.tenant_id, campaign.id)


async def submit_campaign(
    session: AsyncSession, principal: Principal, campaign_id: uuid.UUID
) -> Campaign:
    campaign = await get_campaign_or_404(session, principal.tenant_id, campaign_id)
    if campaign.status not in {CampaignStatus.DRAFT}:
        raise BadRequestError("Only draft campaigns can be submitted for approval.")
    if not campaign.steps:
        raise BadRequestError("A campaign needs at least one step before submission.")

    campaign.status = CampaignStatus.PENDING_APPROVAL
    campaign.approval_status = ApprovalStatus.PENDING
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="campaign.submitted",
        actor_id=principal.user_id,
        entity_type="campaign",
        entity_id=campaign.id,
    )
    await session.commit()
    return await get_campaign_or_404(session, principal.tenant_id, campaign.id)


async def approve_campaign(
    session: AsyncSession, principal: Principal, campaign_id: uuid.UUID
) -> Campaign:
    campaign = await get_campaign_or_404(session, principal.tenant_id, campaign_id)
    if campaign.status != CampaignStatus.PENDING_APPROVAL:
        raise BadRequestError("Only campaigns pending approval can be approved.")

    campaign.status = CampaignStatus.APPROVED
    campaign.approval_status = ApprovalStatus.APPROVED
    campaign.approved_by = principal.user_id
    campaign.approved_at = utcnow()
    for step in campaign.steps:
        step.approval_status = ApprovalStatus.APPROVED
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="campaign.approved",
        actor_id=principal.user_id,
        entity_type="campaign",
        entity_id=campaign.id,
    )
    await session.commit()
    return await get_campaign_or_404(session, principal.tenant_id, campaign.id)


async def reject_campaign(
    session: AsyncSession,
    principal: Principal,
    campaign_id: uuid.UUID,
    *,
    note: str | None = None,
) -> Campaign:
    campaign = await get_campaign_or_404(session, principal.tenant_id, campaign_id)
    if campaign.status != CampaignStatus.PENDING_APPROVAL:
        raise BadRequestError("Only campaigns pending approval can be rejected.")

    campaign.status = CampaignStatus.DRAFT
    campaign.approval_status = ApprovalStatus.REJECTED
    for step in campaign.steps:
        step.approval_status = ApprovalStatus.REJECTED
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="campaign.rejected",
        actor_id=principal.user_id,
        entity_type="campaign",
        entity_id=campaign.id,
        after_data={"note": note},
    )
    await session.commit()
    return await get_campaign_or_404(session, principal.tenant_id, campaign.id)


async def resolve_audience(session: AsyncSession, campaign: Campaign) -> list[Contact]:
    tenant_id = campaign.tenant_id
    if campaign.audience_type == "contacts":
        if not campaign.audience_snapshot:
            return []
        return list(
            (
                await session.scalars(
                    select(Contact).where(
                        Contact.tenant_id == tenant_id,
                        Contact.id.in_([uuid.UUID(value) for value in campaign.audience_snapshot]),
                    )
                )
            ).all()
        )

    if campaign.audience_type == "list" and campaign.audience_id:
        return list(
            (
                await session.scalars(
                    select(Contact)
                    .join(ContactListMember, ContactListMember.contact_id == Contact.id)
                    .where(
                        Contact.tenant_id == tenant_id,
                        ContactListMember.list_id == campaign.audience_id,
                    )
                )
            ).all()
        )

    if campaign.audience_type == "segment" and campaign.audience_id:
        segment = await session.get(Segment, campaign.audience_id)
        if segment is None:
            return []
        definition = segment.definition or {}
        status = definition.get("status")
        stmt = select(Contact).where(Contact.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(Contact.status == status)
        if definition.get("company_id"):
            stmt = stmt.where(Contact.company_id == uuid.UUID(str(definition["company_id"])))
        return list((await session.scalars(stmt)).all())

    return []


async def _resolve_sending_accounts(
    session: AsyncSession, campaign: Campaign
) -> list[EmailAccount]:
    stmt = select(EmailAccount).where(EmailAccount.tenant_id == campaign.tenant_id)
    if campaign.email_account_ids:
        stmt = stmt.where(
            EmailAccount.id.in_([uuid.UUID(value) for value in campaign.email_account_ids])
        )
    stmt = stmt.where(EmailAccount.status == EmailAccountStatus.CONNECTED)
    return list((await session.scalars(stmt)).all())


async def preflight(session: AsyncSession, campaign: Campaign) -> dict[str, Any]:
    settings = get_settings()
    errors: list[str] = []
    warnings: list[str] = []

    if not campaign.steps:
        errors.append("The campaign has no steps.")

    accounts = await _resolve_sending_accounts(session, campaign)
    if not accounts and settings.require_email_account_for_launch:
        errors.append("Connect at least one sending inbox before launching.")

    audience = await resolve_audience(session, campaign)
    sendable: list[uuid.UUID] = []
    suppressed = 0
    for contact in audience:
        if contact.status == ContactStatus.UNSUBSCRIBED:
            suppressed += 1
            continue
        if await is_suppressed(session, campaign.tenant_id, contact.email_normalized):
            suppressed += 1
            continue
        sendable.append(contact.id)

    if not audience:
        errors.append("The campaign audience is empty.")
    elif not sendable:
        errors.append("Every contact in this audience is suppressed.")

    if suppressed:
        warnings.append(f"{suppressed} contact(s) will be skipped due to suppression.")

    if not campaign.email_account_ids:
        warnings.append("No inboxes were selected; connected workspace inboxes will be used.")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "audience_size": len(audience),
        "sendable": len(sendable),
        "sending_accounts": len(accounts),
    }


async def launch_campaign(
    session: AsyncSession, principal: Principal, campaign_id: uuid.UUID
) -> Campaign:
    campaign = await get_campaign_or_404(session, principal.tenant_id, campaign_id)
    if campaign.status != CampaignStatus.APPROVED:
        raise BadRequestError("Only approved campaigns can be launched.")

    result = await preflight(session, campaign)
    if not result["ok"]:
        raise PreflightError(
            "The campaign is not ready to launch.",
            details={"errors": result["errors"], "warnings": result["warnings"]},
        )

    accounts = await _resolve_sending_accounts(session, campaign)
    audience = await resolve_audience(session, campaign)
    enrolled_contact_ids = set(
        (
            await session.scalars(
                select(CampaignEnrollment.contact_id).where(
                    CampaignEnrollment.campaign_id == campaign.id
                )
            )
        ).all()
    )

    account_ids = [account.id for account in accounts]
    account_index = 0
    created = 0
    now = utcnow()
    for contact in audience:
        if contact.id in enrolled_contact_ids:
            continue
        if contact.status == ContactStatus.UNSUBSCRIBED:
            continue
        if await is_suppressed(session, campaign.tenant_id, contact.email_normalized):
            continue
        account_id = None
        if account_ids:
            account_id = account_ids[account_index % len(account_ids)]
            account_index += 1
        session.add(
            CampaignEnrollment(
                tenant_id=principal.tenant_id,
                campaign_id=campaign.id,
                contact_id=contact.id,
                email_account_id=account_id,
                status=EnrollmentStatus.ACTIVE,
                current_step_order=1,
                next_send_at=now,
            )
        )
        created += 1

    campaign.status = CampaignStatus.ACTIVE
    campaign.launched_at = now
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="campaign.launched",
        actor_id=principal.user_id,
        entity_type="campaign",
        entity_id=campaign.id,
        after_data={"enrolled": created, "inboxes": len(account_ids)},
    )
    await session.commit()
    return await get_campaign_or_404(session, principal.tenant_id, campaign.id)


async def set_campaign_status(
    session: AsyncSession,
    principal: Principal,
    campaign_id: uuid.UUID,
    *,
    action: str,
) -> Campaign:
    campaign = await get_campaign_or_404(session, principal.tenant_id, campaign_id)

    if action == "pause":
        if campaign.status != CampaignStatus.ACTIVE:
            raise BadRequestError("Only active campaigns can be paused.")
        campaign.status = CampaignStatus.PAUSED
    elif action == "resume":
        if campaign.status != CampaignStatus.PAUSED:
            raise BadRequestError("Only paused campaigns can be resumed.")
        campaign.status = CampaignStatus.ACTIVE
    elif action == "archive":
        if campaign.status == CampaignStatus.ACTIVE:
            raise BadRequestError("Pause the campaign before archiving it.")
        campaign.status = CampaignStatus.ARCHIVED
    elif action == "complete":
        campaign.status = CampaignStatus.COMPLETED
        campaign.completed_at = utcnow()
    else:
        raise BadRequestError("Unsupported campaign action.")

    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action=f"campaign.{action}",
        actor_id=principal.user_id,
        entity_type="campaign",
        entity_id=campaign.id,
    )
    await session.commit()
    return await get_campaign_or_404(session, principal.tenant_id, campaign.id)


async def delete_campaign(
    session: AsyncSession, principal: Principal, campaign_id: uuid.UUID
) -> None:
    campaign = await get_campaign_or_404(session, principal.tenant_id, campaign_id)
    if campaign.status == CampaignStatus.ACTIVE:
        raise ConflictError("Pause the campaign before deleting it.")

    enrollments = (
        await session.scalars(
            select(CampaignEnrollment).where(CampaignEnrollment.campaign_id == campaign.id)
        )
    ).all()
    for enrollment in enrollments:
        await session.delete(enrollment)

    await session.delete(campaign)
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="campaign.deleted",
        actor_id=principal.user_id,
        entity_type="campaign",
        entity_id=campaign_id,
        before_data={"name": campaign.name},
    )
    await session.commit()


async def preview_campaign_step(
    session: AsyncSession,
    principal: Principal,
    campaign_id: uuid.UUID,
    *,
    contact_id: uuid.UUID,
    step_order: int,
) -> CampaignPreviewResponse:
    campaign = await get_campaign_or_404(session, principal.tenant_id, campaign_id)
    contact = await session.scalar(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.tenant_id == principal.tenant_id,
        )
    )
    if contact is None:
        raise NotFoundError("Contact not found in this workspace.")

    step = next((item for item in campaign.steps if item.step_order == step_order), None)
    if step is None:
        raise NotFoundError("Campaign step not found.")

    tenant = await session.get(Tenant, principal.tenant_id)
    if tenant is None:
        raise NotFoundError("Workspace not found.")

    # AI steps are previewed with the same personalization path the sender uses,
    # so reviewers see exactly what the prospective employee would receive.
    message = await compose_outreach_message(
        session,
        tenant=tenant,
        campaign=campaign,
        step=step,
        contact=contact,
    )
    return CampaignPreviewResponse(
        subject=message.subject,
        body_html=message.body_html,
        body_text=message.body_text,
    )


async def campaign_stats(
    session: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID
) -> CampaignStats:
    await get_campaign_or_404(session, tenant_id, campaign_id)

    async def enrollment_count(status: EnrollmentStatus | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(CampaignEnrollment)
            .where(CampaignEnrollment.campaign_id == campaign_id)
        )
        if status is not None:
            stmt = stmt.where(CampaignEnrollment.status == status)
        return int(await session.scalar(stmt) or 0)

    sent = int(
        await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.campaign_id == campaign_id,
                Message.direction == MessageDirection.OUTBOUND,
            )
        )
        or 0
    )
    replies = int(
        await session.scalar(
            select(func.count()).select_from(Reply).where(Reply.campaign_id == campaign_id)
        )
        or 0
    )
    return CampaignStats(
        enrolled=await enrollment_count(),
        active=await enrollment_count(EnrollmentStatus.ACTIVE),
        stopped=await enrollment_count(EnrollmentStatus.STOPPED),
        completed=await enrollment_count(EnrollmentStatus.COMPLETED),
        sent=sent,
        replies=replies,
    )


async def list_enrollments(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    pagination: Pagination,
    *,
    status: EnrollmentStatus | None = None,
) -> tuple[list[CampaignEnrollment], int]:
    await get_campaign_or_404(session, tenant_id, campaign_id)
    stmt = select(CampaignEnrollment).where(
        CampaignEnrollment.campaign_id == campaign_id,
        CampaignEnrollment.tenant_id == tenant_id,
    )
    count_stmt = (
        select(func.count())
        .select_from(CampaignEnrollment)
        .where(
            CampaignEnrollment.campaign_id == campaign_id,
            CampaignEnrollment.tenant_id == tenant_id,
        )
    )
    if status is not None:
        stmt = stmt.where(CampaignEnrollment.status == status)
        count_stmt = count_stmt.where(CampaignEnrollment.status == status)
    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(CampaignEnrollment.created_at)
        .limit(pagination.capped_limit)
        .offset(pagination.offset)
    )
    return list((await session.scalars(stmt)).all()), total
