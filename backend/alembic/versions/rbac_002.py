"""GCP-parity RBAC: inheritance, versions, binding expiry, deny rules.

Revision ID: rbac_002
Revises: login_codes_001
Create Date: 2026-09-21

Mirrors GCP IAM evaluation:
  - roles.inherit_to_children — folder-level bindings inherited by child
    projects (effective policy = union of own + ancestors).
  - roles.version — etag-style optimistic concurrency for policy writes.
  - user_roles.expires_at — time conditions (request.time < expiry), the most
    common IAM Condition; expired bindings grant nothing.
  - deny_rules — deny policies evaluated BEFORE allow, inherited downward,
    explicit deny always wins (no exceptions in v1).
  - permissions += roles.view (read the policy matrix without assign rights).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "rbac_002"
down_revision: Union[str, Sequence[str], None] = "login_codes_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "inherit_to_children",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "roles",
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default="1"
        ),
    )
    op.add_column(
        "user_roles",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "deny_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_code", sa.String(80), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deny_rules_org", "deny_rules", ["org_id"])
    op.execute(
        sa.text(
            "INSERT INTO permissions (id, code) "
            "VALUES (gen_random_uuid(), 'roles.view') "
            "ON CONFLICT (code) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM permissions WHERE code = 'roles.view'"))
    op.drop_index("ix_deny_rules_org", table_name="deny_rules")
    op.drop_table("deny_rules")
    op.drop_column("user_roles", "expires_at")
    op.drop_column("roles", "version")
    op.drop_column("roles", "inherit_to_children")
