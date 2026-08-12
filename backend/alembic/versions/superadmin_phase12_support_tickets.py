"""superadmin_phase12_support_tickets

Phase 12 — Org-admin support tickets.

Org admins can raise a support request from their admin dashboard; the ticket
lives in the control plane so the super admin team manages a single queue.

  - super_admin.support_tickets: one row per request, owned by the org.
    `status` lifecycle: open -> in_progress -> resolved (+ optional reply note).
  - Internal creation/list is gated by a shared service key, so the tenant
    backend can create/list tickets for a specific org on behalf of its
    authenticated org admin.
  - New permissions:
      support.tickets.view    — see the ticket queue
      support.tickets.manage  — change status / reply
    Granted: view to owner/super_admin/operations/support;
    manage to owner/super_admin/operations.

Revision ID: superadmin_phase12_support_tickets
Revises: superadmin_phase11_impersonation_approval
Create Date: 2026-08-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase12_support_tickets"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase11_impersonation_approval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VIEW_ROLES = ("owner", "super_admin", "operations", "support")
MANAGE_ROLES = ("owner", "super_admin", "operations")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.support_tickets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id UUID NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
            org_name VARCHAR(255),
            reporter_email VARCHAR(255) NOT NULL,
            reporter_name VARCHAR(150),
            subject VARCHAR(255) NOT NULL,
            detail TEXT NOT NULL,
            priority VARCHAR(20) NOT NULL DEFAULT 'normal',
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            reply TEXT,
            replied_by_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            replied_by_email VARCHAR(255),
            replied_at TIMESTAMPTZ,
            resolved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_support_tickets_status
        ON super_admin.support_tickets (status, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_support_tickets_org
        ON super_admin.support_tickets (org_id, created_at DESC)
        """
    )

    for code, module, name, desc in [
        (
            "support.tickets.view",
            "support",
            "View Support Tickets",
            "List the org support ticket queue",
        ),
        (
            "support.tickets.manage",
            "support",
            "Manage Support Tickets",
            "Change ticket status and reply to org admins",
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

    for role_code in VIEW_ROLES:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.role_permissions (role_id, permission_id)
                SELECT r.id, p.id
                FROM super_admin.roles r, super_admin.permissions p
                WHERE r.code = :rc AND p.code = 'support.tickets.view'
                ON CONFLICT DO NOTHING
                """
            ),
            {"rc": role_code},
        )
    for role_code in MANAGE_ROLES:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.role_permissions (role_id, permission_id)
                SELECT r.id, p.id
                FROM super_admin.roles r, super_admin.permissions p
                WHERE r.code = :rc AND p.code = 'support.tickets.manage'
                ON CONFLICT DO NOTHING
                """
            ),
            {"rc": role_code},
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM super_admin.role_permissions WHERE permission_id IN "
        "(SELECT id FROM super_admin.permissions WHERE code IN "
        "('support.tickets.view', 'support.tickets.manage'))"
    )
    op.execute(
        "DELETE FROM super_admin.permissions WHERE code IN "
        "('support.tickets.view', 'support.tickets.manage')"
    )
    op.execute("DROP INDEX IF EXISTS ix_support_tickets_status")
    op.execute("DROP INDEX IF EXISTS ix_support_tickets_org")
    op.execute("DROP TABLE IF EXISTS super_admin.support_tickets")