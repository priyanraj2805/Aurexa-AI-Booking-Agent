"""superadmin_phase8_support_security

Phase 8 — Support & Security (control plane side).

  - super_admin.impersonations: every impersonation started by an admin.
    The raw token is NEVER stored — only the jti (revocation handle).
    Single active session is enforced here: starting a new impersonation
    ends any prior active one for the same admin.
  - super_admin.security_settings: single-row (id=1) security policy store:
    MFA enforcement, password policy, IP allowlist.
  - New permissions:
      support.conversations.view  — read-only conversation peek (masked)
      security.manage             — edit the security policy
    Granted: conversations.view to support/operations/owner/super_admin;
    security.manage to owner/super_admin only.

Revision ID: superadmin_phase8_support_security
Revises: superadmin_phase7_ops_tools
Create Date: 2026-08-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase8_support_security"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase8_impersonation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONVERSATIONS_ROLES = ("owner", "super_admin", "operations", "support")
SECURITY_ROLES = ("owner", "super_admin")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.impersonations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            admin_id UUID NOT NULL REFERENCES super_admin.super_admins (id) ON DELETE CASCADE,
            org_id UUID NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
            org_name VARCHAR(255),
            user_email VARCHAR(255) NOT NULL,
            token_jti VARCHAR(64) NOT NULL UNIQUE,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            ended_at TIMESTAMPTZ,
            ended_by UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_impersonations_admin
        ON super_admin.impersonations (admin_id, status)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.security_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            mfa_enforced BOOLEAN NOT NULL DEFAULT TRUE,
            password_min_length INTEGER NOT NULL DEFAULT 8,
            password_require_upper BOOLEAN NOT NULL DEFAULT TRUE,
            password_require_digit BOOLEAN NOT NULL DEFAULT TRUE,
            password_require_special BOOLEAN NOT NULL DEFAULT TRUE,
            ip_allowlist_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            ip_allowlist_cidrs JSONB NOT NULL DEFAULT '[]'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL
        )
        """
    )
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO super_admin.security_settings (id)
            VALUES (1)
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    for code, module, name, desc in [
        (
            "support.conversations.view",
            "support",
            "View Conversations",
            "Read conversation transcripts with PII masked",
        ),
        (
            "security.manage",
            "security",
            "Manage Security Policy",
            "Edit MFA, password policy and IP allowlist settings",
        ),
    ]:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.permissions (id, code, module, name, description)
                VALUES (gen_random_uuid(), :code, :module, :name, :description)
                ON CONFLICT (code) DO NOTHING
                """,
            ),
            {"code": code, "module": module, "name": name, "description": desc},
        )

    for role_code in CONVERSATIONS_ROLES:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.role_permissions (role_id, permission_id)
                SELECT r.id, p.id
                FROM super_admin.roles r, super_admin.permissions p
                WHERE r.code = :rc AND p.code = 'support.conversations.view'
                ON CONFLICT DO NOTHING
                """
            ),
            {"rc": role_code},
        )
    for role_code in SECURITY_ROLES:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.role_permissions (role_id, permission_id)
                SELECT r.id, p.id
                FROM super_admin.roles r, super_admin.permissions p
                WHERE r.code = :rc AND p.code = 'security.manage'
                ON CONFLICT DO NOTHING
                """
            ),
            {"rc": role_code},
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS super_admin.impersonations")
    op.execute("DROP TABLE IF EXISTS super_admin.security_settings")
    op.execute(
        "DELETE FROM super_admin.permissions WHERE code IN "
        "('support.conversations.view', 'security.manage')"
    )
