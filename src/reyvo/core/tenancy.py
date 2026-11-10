from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from reyvo.db.models import Membership, Tenant, User


@dataclass(slots=True)
class Principal:
    user: User
    tenant: Tenant
    membership: Membership

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    @property
    def role(self) -> str:
        return self.membership.role.value

    @property
    def is_platform_admin(self) -> bool:
        return self.user.is_platform_admin


async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Set the PostgreSQL session variable used by row-level security policies.

    SQLite (used by the test suite) does not support this, so it is skipped.
    """

    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
