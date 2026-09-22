"""RBAC tables + permission seeds (shared by tenant + AdminPlus planes)

Revision ID: rbac_001
Revises: units_providers_001
Create Date: 2026-09-21

Five-table model (org-scoped, mirrors backend-super RBAC):
  - permissions (global codes, resource.action — stable API contracts)
  - roles (per-org bundles, code unique within org)
  - role_permissions (which permissions each role grants)
  - user_roles (who holds which role in which org + granted_by audit)

Roles themselves are seeded lazily at login (ensure_org_roles) so every
environment gets them without data-migration reruns; permissions are global
and seeded here. Existing behavior is preserved: orgs without roles behave
as before until first login seeds Owner.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "rbac_001"
down_revision: Union[str, Sequence[str], None] = "units_providers_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Canonical permission codes — must match app/core/rbac_seed.py.
TENANT_PERMISSIONS = [
    "bookings.view", "bookings.manage", "bookings.cancel",
    "schedule.view", "schedule.manage",
    "catalog.view", "catalog.manage",
    "inbox.view", "inbox.manage",
    "integrations.view", "integrations.manage",
    "billing.view",
    "users.view", "users.manage",
    "roles.assign",
    "reports.view",
    "settings.view", "settings.manage",
]

ADMINPLUS_PERMISSIONS = [
    "members.view", "members.manage", "members.suspend",
    "catalog.manage",
    "oversight.view",
    "policies.manage",
    "billing.view",
    "roles.assign",
]


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_permissions_code"),
    )

    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "code", name="uq_roles_org_code"),
    )
    op.create_index("ix_roles_org", "roles", ["org_id"])

    op.create_table(
        "role_permissions",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id", "org_id"),
    )
    op.create_index("ix_user_roles_user_org", "user_roles", ["user_id", "org_id"])

    # NOTE: op.execute() takes no bind params — codes are hardcoded constants
    # ([a-z.]), so literal interpolation is safe here.
    for code in TENANT_PERMISSIONS + ADMINPLUS_PERMISSIONS:
        op.execute(
            sa.text(
                "INSERT INTO permissions (id, code) "
                "VALUES (gen_random_uuid(), '%s') "
                "ON CONFLICT (code) DO NOTHING" % code
            )
        )


def downgrade() -> None:
    op.drop_index("ix_user_roles_user_org", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_index("ix_roles_org", table_name="roles")
    op.drop_table("roles")
    op.drop_table("permissions")
