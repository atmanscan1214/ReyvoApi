from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.exceptions import BadRequestError, ConflictError, NotFoundError
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.core.utils import is_valid_email, normalize_email, utcnow
from reyvo.db.enums import ContactStatus, ImportStatus
from reyvo.db.models import (
    Company,
    Contact,
    ContactList,
    ContactListMember,
    ImportJob,
)
from reyvo.schemas.contact import (
    ContactCreate,
    ContactListCreate,
    ContactUpdate,
    ImportErrorRow,
    ImportReport,
)
from reyvo.services import audit_service, company_service
from reyvo.services.suppression_service import is_suppressed


def _display_name(
    *,
    full_name: str | None,
    first_name: str | None,
    last_name: str | None,
    email: str,
) -> str:
    if full_name and full_name.strip():
        return full_name.strip()
    joined = " ".join(part for part in (first_name, last_name) if part).strip()
    if joined:
        return joined
    return email.split("@", 1)[0]


async def list_contacts(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pagination: Pagination,
    *,
    search: str | None = None,
    status: ContactStatus | None = None,
    company_id: uuid.UUID | None = None,
    list_id: uuid.UUID | None = None,
) -> tuple[list[Contact], int]:
    stmt = select(Contact).where(Contact.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(Contact).where(Contact.tenant_id == tenant_id)

    if list_id is not None:
        stmt = stmt.join(ContactListMember, ContactListMember.contact_id == Contact.id).where(
            ContactListMember.list_id == list_id,
            ContactListMember.tenant_id == tenant_id,
        )
        count_stmt = count_stmt.join(
            ContactListMember, ContactListMember.contact_id == Contact.id
        ).where(
            ContactListMember.list_id == list_id,
            ContactListMember.tenant_id == tenant_id,
        )
    if search:
        pattern = f"%{search.strip().lower()}%"
        condition = or_(
            Contact.email_normalized.like(pattern),
            func.lower(Contact.full_name).like(pattern),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if status is not None:
        stmt = stmt.where(Contact.status == status)
        count_stmt = count_stmt.where(Contact.status == status)
    if company_id is not None:
        stmt = stmt.where(Contact.company_id == company_id)
        count_stmt = count_stmt.where(Contact.company_id == company_id)

    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(Contact.created_at.desc())
        .limit(pagination.capped_limit)
        .offset(pagination.offset)
    )
    return list((await session.scalars(stmt)).all()), total


async def get_contact_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, contact_id: uuid.UUID
) -> Contact:
    contact = await session.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
    )
    if contact is None:
        raise NotFoundError("Contact not found.")
    return contact


async def _resolve_company(
    session: AsyncSession,
    principal: Principal,
    *,
    company_id: uuid.UUID | None,
    company_name: str | None,
    company_domain: str | None,
) -> uuid.UUID | None:
    if company_id is not None:
        company = await session.scalar(
            select(Company).where(
                Company.id == company_id,
                Company.tenant_id == principal.tenant_id,
            )
        )
        if company is None:
            raise BadRequestError("The selected company does not exist in this workspace.")
        return company.id
    company = await company_service.find_or_create_company(
        session, principal, name=company_name, domain=company_domain
    )
    return company.id if company is not None else None


async def create_contact(
    session: AsyncSession,
    principal: Principal,
    payload: ContactCreate,
    *,
    commit: bool = True,
) -> Contact:
    email = normalize_email(str(payload.email))
    if not is_valid_email(email):
        raise BadRequestError("A valid email address is required.")

    existing = await session.scalar(
        select(Contact).where(
            Contact.tenant_id == principal.tenant_id,
            Contact.email_normalized == email,
        )
    )
    if existing is not None:
        raise ConflictError("A contact with this email already exists in this workspace.")

    company_id = await _resolve_company(
        session,
        principal,
        company_id=payload.company_id,
        company_name=payload.company_name,
        company_domain=payload.company_domain,
    )

    suppressed = await is_suppressed(session, principal.tenant_id, email)
    contact = Contact(
        tenant_id=principal.tenant_id,
        company_id=company_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        full_name=_display_name(
            full_name=payload.full_name,
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=email,
        ),
        title=payload.title,
        email=str(payload.email).strip(),
        email_normalized=email,
        email_status=payload.email_status,
        phone=payload.phone,
        linkedin_url=payload.linkedin_url,
        source=payload.source,
        status=ContactStatus.UNSUBSCRIBED if suppressed else ContactStatus.NEW,
        notes=payload.notes,
        custom_fields=payload.custom_fields,
    )
    session.add(contact)
    await session.flush()
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="contact.created",
        actor_id=principal.user_id,
        entity_type="contact",
        entity_id=contact.id,
        after_data={"email": contact.email_normalized, "company_id": str(company_id or "")},
    )
    if commit:
        await session.commit()
        await session.refresh(contact)
    return contact


async def update_contact(
    session: AsyncSession,
    principal: Principal,
    contact_id: uuid.UUID,
    payload: ContactUpdate,
) -> Contact:
    contact = await get_contact_or_404(session, principal.tenant_id, contact_id)
    data = payload.model_dump(exclude_unset=True)

    if "email" in data and data["email"] is not None:
        email = normalize_email(str(data["email"]))
        if not is_valid_email(email):
            raise BadRequestError("A valid email address is required.")
        duplicate = await session.scalar(
            select(Contact).where(
                Contact.tenant_id == principal.tenant_id,
                Contact.email_normalized == email,
                Contact.id != contact.id,
            )
        )
        if duplicate is not None:
            raise ConflictError("Another contact already uses this email address.")
        data["email"] = str(data["email"]).strip()
        data["email_normalized"] = email

    if "company_id" in data and data["company_id"] is not None:
        company = await session.scalar(
            select(Company).where(
                Company.id == data["company_id"],
                Company.tenant_id == principal.tenant_id,
            )
        )
        if company is None:
            raise BadRequestError("The selected company does not exist in this workspace.")

    for field, value in data.items():
        setattr(contact, field, value)

    if "full_name" not in data and ("first_name" in data or "last_name" in data):
        contact.full_name = _display_name(
            full_name=None,
            first_name=contact.first_name,
            last_name=contact.last_name,
            email=contact.email_normalized,
        )

    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="contact.updated",
        actor_id=principal.user_id,
        entity_type="contact",
        entity_id=contact.id,
        after_data={key: str(value) for key, value in data.items()},
    )
    await session.commit()
    await session.refresh(contact)
    return contact


async def delete_contact(
    session: AsyncSession, principal: Principal, contact_id: uuid.UUID
) -> None:
    contact = await get_contact_or_404(session, principal.tenant_id, contact_id)
    await session.delete(contact)
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="contact.deleted",
        actor_id=principal.user_id,
        entity_type="contact",
        entity_id=contact_id,
        before_data={"email": contact.email_normalized},
    )
    await session.commit()


_CONTACT_CSV_COLUMNS = [
    "email",
    "first_name",
    "last_name",
    "full_name",
    "title",
    "company",
    "company_domain",
    "phone",
    "linkedin_url",
    "source",
]


async def import_contacts_csv(
    session: AsyncSession,
    principal: Principal,
    *,
    filename: str,
    content: bytes,
) -> ImportReport:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BadRequestError("CSV files must be UTF-8 encoded.") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise BadRequestError("The CSV file has no header row.")
    normalized_headers = {header.strip().lower(): header for header in reader.fieldnames}
    email_column = normalized_headers.get("email")
    if email_column is None:
        raise BadRequestError("The CSV file must include an 'email' column.")

    job = ImportJob(
        tenant_id=principal.tenant_id,
        filename=filename[:255],
        status=ImportStatus.PROCESSING,
        mapping={key: value for key, value in normalized_headers.items() if value != key},
        created_by=principal.user_id,
    )
    session.add(job)
    await session.flush()

    errors: list[ImportErrorRow] = []
    imported = duplicates = suppressed_count = 0
    row_count = 0
    seen_emails: set[str] = set()

    def value(row: dict[str, str], name: str) -> str | None:
        header = normalized_headers.get(name)
        if header is None:
            return None
        raw = row.get(header)
        if raw is None:
            return None
        cleaned = raw.strip()
        return cleaned or None

    for index, row in enumerate(reader, start=2):
        row_count += 1
        email = value(row, "email")
        if not email or not is_valid_email(email):
            errors.append(ImportErrorRow(row=index, email=email, reason="invalid_email"))
            continue
        email = normalize_email(email)

        if email in seen_emails:
            duplicates += 1
            continue
        seen_emails.add(email)

        existing = await session.scalar(
            select(Contact.id).where(
                Contact.tenant_id == principal.tenant_id,
                Contact.email_normalized == email,
            )
        )
        if existing is not None:
            duplicates += 1
            continue
        if await is_suppressed(session, principal.tenant_id, email):
            suppressed_count += 1
            continue

        first_name = value(row, "first_name")
        last_name = value(row, "last_name")
        company_id = await _resolve_company(
            session,
            principal,
            company_id=None,
            company_name=value(row, "company"),
            company_domain=value(row, "company_domain"),
        )
        contact = Contact(
            tenant_id=principal.tenant_id,
            company_id=company_id,
            first_name=first_name,
            last_name=last_name,
            full_name=_display_name(
                full_name=value(row, "full_name"),
                first_name=first_name,
                last_name=last_name,
                email=email,
            ),
            title=value(row, "title"),
            email=email,
            email_normalized=email,
            phone=value(row, "phone"),
            linkedin_url=value(row, "linkedin_url"),
            source=value(row, "source") or "csv_import",
            status=ContactStatus.NEW,
            custom_fields={},
        )
        session.add(contact)
        imported += 1

    await session.flush()

    job.row_count = row_count
    job.imported_count = imported
    job.duplicate_count = duplicates
    job.suppressed_count = suppressed_count
    job.error_count = len(errors)
    job.status = ImportStatus.COMPLETED
    job.completed_at = utcnow()
    job.report = {"errors": [error.model_dump() for error in errors]}
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="contacts.imported",
        actor_id=principal.user_id,
        entity_type="import",
        entity_id=job.id,
        after_data={
            "row_count": row_count,
            "imported": imported,
            "duplicates": duplicates,
            "suppressed": suppressed_count,
            "errors": len(errors),
        },
    )
    await session.commit()

    return ImportReport(
        import_id=job.id,
        status=job.status.value,
        row_count=row_count,
        imported_count=imported,
        duplicate_count=duplicates,
        suppressed_count=suppressed_count,
        error_count=len(errors),
        errors=errors,
    )


async def export_contacts_csv(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    stmt = (
        select(Contact, Company.name)
        .outerjoin(Company, Company.id == Contact.company_id)
        .where(Contact.tenant_id == tenant_id)
        .order_by(Contact.created_at)
    )
    rows = (await session.execute(stmt)).all()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[*_CONTACT_CSV_COLUMNS, "status", "created_at"])
    writer.writeheader()
    for contact, company_name in rows:
        writer.writerow(
            {
                "email": contact.email_normalized,
                "first_name": contact.first_name or "",
                "last_name": contact.last_name or "",
                "full_name": contact.full_name,
                "title": contact.title or "",
                "company": company_name or "",
                "company_domain": "",
                "phone": contact.phone or "",
                "linkedin_url": contact.linkedin_url or "",
                "source": contact.source or "",
                "status": contact.status.value,
                "created_at": contact.created_at.isoformat(),
            }
        )
    return output.getvalue()


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


async def create_list(
    session: AsyncSession, principal: Principal, payload: ContactListCreate
) -> ContactList:
    existing = await session.scalar(
        select(ContactList).where(
            ContactList.tenant_id == principal.tenant_id,
            ContactList.name == payload.name,
        )
    )
    if existing is not None:
        raise ConflictError("A list with this name already exists.")

    contact_list = ContactList(
        tenant_id=principal.tenant_id,
        name=payload.name.strip(),
        description=payload.description,
    )
    session.add(contact_list)
    await session.flush()

    if payload.contact_ids:
        await _add_members(session, principal, contact_list.id, payload.contact_ids)

    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="list.created",
        actor_id=principal.user_id,
        entity_type="contact_list",
        entity_id=contact_list.id,
        after_data={"name": contact_list.name, "contacts": len(payload.contact_ids)},
    )
    await session.commit()
    await session.refresh(contact_list)
    return contact_list


async def list_lists(session: AsyncSession, tenant_id: uuid.UUID) -> list[tuple[ContactList, int]]:
    stmt = (
        select(ContactList, func.count(ContactListMember.id))
        .outerjoin(ContactListMember, ContactListMember.list_id == ContactList.id)
        .where(ContactList.tenant_id == tenant_id)
        .group_by(ContactList.id)
        .order_by(ContactList.name)
    )
    return [(row[0], int(row[1])) for row in (await session.execute(stmt)).all()]


async def get_list_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, list_id: uuid.UUID
) -> ContactList:
    contact_list = await session.scalar(
        select(ContactList).where(ContactList.id == list_id, ContactList.tenant_id == tenant_id)
    )
    if contact_list is None:
        raise NotFoundError("Contact list not found.")
    return contact_list


async def _add_members(
    session: AsyncSession,
    principal: Principal,
    list_id: uuid.UUID,
    contact_ids: list[uuid.UUID],
) -> int:
    unique_ids = list(dict.fromkeys(contact_ids))
    if not unique_ids:
        return 0
    valid_ids = set(
        (
            await session.scalars(
                select(Contact.id).where(
                    Contact.tenant_id == principal.tenant_id,
                    Contact.id.in_(unique_ids),
                )
            )
        ).all()
    )
    existing_ids = set(
        (
            await session.scalars(
                select(ContactListMember.contact_id).where(
                    ContactListMember.tenant_id == principal.tenant_id,
                    ContactListMember.list_id == list_id,
                    ContactListMember.contact_id.in_(valid_ids or [uuid.uuid4()]),
                )
            )
        ).all()
    )
    added = 0
    for contact_id in unique_ids:
        if contact_id not in valid_ids or contact_id in existing_ids:
            continue
        session.add(
            ContactListMember(
                tenant_id=principal.tenant_id,
                list_id=list_id,
                contact_id=contact_id,
            )
        )
        added += 1
    return added


async def add_list_contacts(
    session: AsyncSession,
    principal: Principal,
    list_id: uuid.UUID,
    contact_ids: list[uuid.UUID],
) -> int:
    await get_list_or_404(session, principal.tenant_id, list_id)
    added = await _add_members(session, principal, list_id, contact_ids)
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="list.contacts_added",
        actor_id=principal.user_id,
        entity_type="contact_list",
        entity_id=list_id,
        after_data={"added": added},
    )
    await session.commit()
    return added


async def remove_list_contact(
    session: AsyncSession,
    principal: Principal,
    list_id: uuid.UUID,
    contact_id: uuid.UUID,
) -> None:
    await get_list_or_404(session, principal.tenant_id, list_id)
    member = await session.scalar(
        select(ContactListMember).where(
            ContactListMember.tenant_id == principal.tenant_id,
            ContactListMember.list_id == list_id,
            ContactListMember.contact_id == contact_id,
        )
    )
    if member is None:
        raise NotFoundError("This contact is not in the list.")
    await session.delete(member)
    await session.commit()


async def delete_list(session: AsyncSession, principal: Principal, list_id: uuid.UUID) -> None:
    contact_list = await get_list_or_404(session, principal.tenant_id, list_id)
    await session.delete(contact_list)
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="list.deleted",
        actor_id=principal.user_id,
        entity_type="contact_list",
        entity_id=list_id,
        before_data={"name": contact_list.name},
    )
    await session.commit()


async def list_contacts_for_list(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    list_id: uuid.UUID,
    pagination: Pagination,
) -> tuple[list[Contact], int]:
    return await list_contacts(session, tenant_id, pagination, list_id=list_id)


async def contact_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": row.get("email"),
        "first_name": row.get("first_name"),
        "last_name": row.get("last_name"),
        "full_name": row.get("full_name"),
        "title": row.get("title"),
        "phone": row.get("phone"),
        "linkedin_url": row.get("linkedin_url"),
        "source": row.get("source"),
    }
