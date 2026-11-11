from __future__ import annotations

from fastapi import APIRouter

from reyvo.api.v1 import (
    admin,
    ai,
    auth,
    billing,
    campaigns,
    companies,
    contacts,
    dashboard,
    email_accounts,
    opportunities,
    public,
    replies,
    suppressions,
    tasks,
    tenants,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tenants.router)
api_router.include_router(companies.router)
api_router.include_router(contacts.router)
api_router.include_router(contacts.lists_router)
api_router.include_router(suppressions.router)
api_router.include_router(email_accounts.router)
api_router.include_router(campaigns.router)
api_router.include_router(replies.router)
api_router.include_router(tasks.router)
api_router.include_router(opportunities.router)
api_router.include_router(dashboard.router)
api_router.include_router(ai.router)
api_router.include_router(admin.router)
api_router.include_router(billing.router)
api_router.include_router(public.router)
api_router.include_router(webhooks.router)
