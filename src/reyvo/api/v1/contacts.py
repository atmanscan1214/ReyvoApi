from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.api.deps import get_pagination, get_principal
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.enums import ContactStatus
from reyvo.db.session import get_db
from reyvo.schemas.common import MessageResponse, Page
from reyvo.schemas.contact import (
    ContactCreate,
    ContactListCreate,
    ContactListMembersAdd,
    ContactListRead,
    ContactListSummary,
    ContactRead,
    ContactUpdate,
    ImportReport,
)
from reyvo.services import contact_service

router = APIRouter(prefix="/contacts", tags=["contacts"])
lists_router = APIRouter(prefix="/lists", tags=["lists"])


@router.get("", response_model=Page[ContactRead])
async def list_contacts(
    search: str | None = Query(default=None, max_length=255),
    status_filter: ContactStatus | None = Query(default=None, alias="status"),
    company_id: uuid.UUID | None = Query(default=None),
    list_id: uuid.UUID | None = Query(default=None),
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Page[ContactRead]:
    items, total = await contact_service.list_contacts(
        session,
        principal.tenant_id,
        pagination,
        search=search,
        status=status_filter,
        company_id=company_id,
        list_id=list_id,
    )
    return Page[ContactRead](
        items=[ContactRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@router.post("/import", response_model=ImportReport, status_code=status.HTTP_201_CREATED)
async def import_contacts(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ImportReport:
    content = await file.read()
    return await contact_service.import_contacts_csv(
        session,
        principal,
        filename=file.filename or "contacts.csv",
        content=content,
    )


@router.get("/export")
async def export_contacts(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    csv_text = await contact_service.export_contacts_csv(session, principal.tenant_id)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="contacts.csv"'},
    )


@router.post("", response_model=ContactRead, status_code=status.HTTP_201_CREATED)
async def create_contact(
    payload: ContactCreate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ContactRead:
    contact = await contact_service.create_contact(session, principal, payload)
    return ContactRead.model_validate(contact)


@router.get("/{contact_id}", response_model=ContactRead)
async def get_contact(
    contact_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ContactRead:
    contact = await contact_service.get_contact_or_404(session, principal.tenant_id, contact_id)
    return ContactRead.model_validate(contact)


@router.patch("/{contact_id}", response_model=ContactRead)
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ContactRead:
    contact = await contact_service.update_contact(session, principal, contact_id, payload)
    return ContactRead.model_validate(contact)


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    await contact_service.delete_contact(session, principal, contact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


@lists_router.get("", response_model=list[ContactListSummary])
async def list_lists(
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[ContactListSummary]:
    rows = await contact_service.list_lists(session, principal.tenant_id)
    return [
        ContactListSummary(
            **ContactListRead.model_validate(contact_list).model_dump(),
            contact_count=count,
        )
        for contact_list, count in rows
    ]


@lists_router.post("", response_model=ContactListRead, status_code=status.HTTP_201_CREATED)
async def create_list(
    payload: ContactListCreate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ContactListRead:
    contact_list = await contact_service.create_list(session, principal, payload)
    return ContactListRead.model_validate(contact_list)


@lists_router.get("/{list_id}/contacts", response_model=Page[ContactRead])
async def list_list_contacts(
    list_id: uuid.UUID,
    pagination: Pagination = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Page[ContactRead]:
    items, total = await contact_service.list_contacts_for_list(
        session, principal.tenant_id, list_id, pagination
    )
    return Page[ContactRead](
        items=[ContactRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.capped_limit,
        offset=pagination.offset,
    )


@lists_router.post("/{list_id}/contacts", response_model=MessageResponse)
async def add_list_contacts(
    list_id: uuid.UUID,
    payload: ContactListMembersAdd,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> MessageResponse:
    added = await contact_service.add_list_contacts(
        session, principal, list_id, payload.contact_ids
    )
    return MessageResponse(message=f"Added {added} contact(s) to the list.")


@lists_router.delete("/{list_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_list_contact(
    list_id: uuid.UUID,
    contact_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    await contact_service.remove_list_contact(session, principal, list_id, contact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@lists_router.delete("/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_list(
    list_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    await contact_service.delete_list(session, principal, list_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
