"""add workos sso fields

Revision ID: 7d226fe14b46
Revises: a7ca34fde91a
Create Date: 2026-11-10 13:42:01.793573

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d226fe14b46"
down_revision: str | None = "a7ca34fde91a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("workos_organization_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("workos_sso_connection_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        op.f("ix_tenants_workos_organization_id"),
        "tenants",
        ["workos_organization_id"],
        unique=True,
    )

    op.add_column(
        "users",
        sa.Column("workos_user_id", sa.String(length=255), nullable=True),
    )
    # Existing local accounts must keep working, so default the new column.
    op.add_column(
        "users",
        sa.Column(
            "auth_provider",
            sa.String(length=40),
            nullable=False,
            server_default=sa.text("'local'"),
        ),
    )
    op.create_index(
        op.f("ix_users_workos_user_id"),
        "users",
        ["workos_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_workos_user_id"), table_name="users")
    op.drop_column("users", "auth_provider")
    op.drop_column("users", "workos_user_id")
    op.drop_index(op.f("ix_tenants_workos_organization_id"), table_name="tenants")
    op.drop_column("tenants", "workos_sso_connection_id")
    op.drop_column("tenants", "workos_organization_id")
