from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from reyvo.core.exceptions import NotFoundError
from reyvo.core.pagination import Pagination
from reyvo.core.tenancy import Principal
from reyvo.db.models import Company, Contact
from reyvo.schemas.contact import CompanyCreate, CompanyUpdate
from reyvo.services import audit_service


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    domain = value.strip().lower()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix) :]
    domain = domain.split("/", 1)[0]
    return domain or None


async def list_companies(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pagination: Pagination,
    *,
    search: str | None = None,
    industry: str | None = None,
) -> tuple[list[Company], int]:
    stmt = select(Company).where(Company.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(Company).where(Company.tenant_id == tenant_id)

    if search:
        pattern = f"%{search.strip().lower()}%"
        condition = or_(
            Company.normalized_name.like(pattern),
            Company.normalized_domain.like(pattern),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if industry:
        stmt = stmt.where(Company.industry == industry)
        count_stmt = count_stmt.where(Company.industry == industry)

    total = int(await session.scalar(count_stmt) or 0)
    stmt = stmt.order_by(Company.name).limit(pagination.capped_limit).offset(pagination.offset)
    return list((await session.scalars(stmt)).all()), total


async def get_company_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, company_id: uuid.UUID
) -> Company:
    company = await session.scalar(
        select(Company).where(Company.id == company_id, Company.tenant_id == tenant_id)
    )
    if company is None:
        raise NotFoundError("Company not found.")
    return company


async def create_company(
    session: AsyncSession,
    principal: Principal,
    payload: CompanyCreate,
    *,
    commit: bool = True,
) -> Company:
    domain = normalize_domain(payload.domain)
    company = Company(
        tenant_id=principal.tenant_id,
        name=payload.name.strip(),
        normalized_name=payload.name.strip().lower(),
        domain=domain,
        normalized_domain=domain,
        industry=payload.industry,
        employee_count=payload.employee_count,
        city=payload.city,
        state=payload.state,
        country=payload.country,
        website=payload.website,
        linkedin_url=payload.linkedin_url,
        source=payload.source,
        notes=payload.notes,
        custom_fields=payload.custom_fields,
    )
    session.add(company)
    await session.flush()
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="company.created",
        actor_id=principal.user_id,
        entity_type="company",
        entity_id=company.id,
        after_data={"name": company.name},
    )
    if commit:
        await session.commit()
        await session.refresh(company)
    return company


async def update_company(
    session: AsyncSession,
    principal: Principal,
    company_id: uuid.UUID,
    payload: CompanyUpdate,
) -> Company:
    company = await get_company_or_404(session, principal.tenant_id, company_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        data["normalized_name"] = data["name"].strip().lower()
    if "domain" in data:
        data["domain"] = normalize_domain(data["domain"])
        data["normalized_domain"] = data["domain"]
    for field, value in data.items():
        setattr(company, field, value)
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="company.updated",
        actor_id=principal.user_id,
        entity_type="company",
        entity_id=company.id,
        after_data=data,
    )
    await session.commit()
    await session.refresh(company)
    return company


async def delete_company(
    session: AsyncSession, principal: Principal, company_id: uuid.UUID
) -> None:
    company = await get_company_or_404(session, principal.tenant_id, company_id)
    contact_count = int(
        await session.scalar(
            select(func.count())
            .select_from(Contact)
            .where(
                Contact.tenant_id == principal.tenant_id,
                Contact.company_id == company.id,
            )
        )
        or 0
    )
    if contact_count:
        # Detach contacts instead of deleting people when a company is removed.
        contacts = (
            await session.scalars(
                select(Contact).where(
                    Contact.tenant_id == principal.tenant_id,
                    Contact.company_id == company.id,
                )
            )
        ).all()
        for contact in contacts:
            contact.company_id = None
    await session.delete(company)
    await audit_service.record(
        session,
        tenant_id=principal.tenant_id,
        action="company.deleted",
        actor_id=principal.user_id,
        entity_type="company",
        entity_id=company_id,
        before_data={"name": company.name, "detached_contacts": contact_count},
    )
    await session.commit()


async def find_or_create_company(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str | None,
    domain: str | None,
) -> Company | None:
    normalized_domain = normalize_domain(domain)
    if normalized_domain:
        existing = await session.scalar(
            select(Company).where(
                Company.tenant_id == principal.tenant_id,
                Company.normalized_domain == normalized_domain,
            )
        )
        if existing is not None:
            return existing
    if name:
        existing = await session.scalar(
            select(Company).where(
                Company.tenant_id == principal.tenant_id,
                Company.normalized_name == name.strip().lower(),
            )
        )
        if existing is not None:
            return existing
    if not name and not normalized_domain:
        return None
    return await create_company(
        session,
        principal,
        CompanyCreate(name=name or normalized_domain or "Unknown company", domain=domain),
        commit=False,
    )
