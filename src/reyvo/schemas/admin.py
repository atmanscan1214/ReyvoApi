from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from reyvo.db.enums import TenantStatus
from reyvo.schemas.common import ORMModel


class AdminTenantRead(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    status: TenantStatus
    plan_code: str
    timezone: str
    created_at: datetime
    updated_at: datetime
    trial_ends_at: datetime | None


class AdminTenantUpdate(BaseModel):
    status: TenantStatus | None = None
    plan_code: str | None = None
