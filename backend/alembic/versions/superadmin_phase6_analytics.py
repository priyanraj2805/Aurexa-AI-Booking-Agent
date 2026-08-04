"""superadmin_phase6_analytics

Phase 6 — Analytics & Monitoring (backend): permissions for the report
engine, dependency health, platform alerts and incidents, plus the
incidents table.

  - super_admin.incidents: platform incidents with lifecycle
    open -> investigating -> resolved.
  - New permissions:
      reports.view, health.view, alerts.view, alerts.manage,
      incidents.view, incidents.manage
    owner/super_admin get all; operations gets the read-only trio
    (reports.view, health.view, alerts.view); support gets nothing.

Revision ID: superadmin_phase6_analytics
Revises: superadmin_phase5_billing
Create Date: 2026-08-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase6_analytics"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase5_billing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALL_PERMS = (
    "reports.view",
    "health.view",
    "alerts.view",
    "alerts.manage",
    "incidents.view",
    "incidents.manage",
)
READ_ONLY_PERMS = ("reports.view", "health.view", "alerts.view", "incidents.view")

PERM_META = {
    "reports.view": ("reports", "View Reports", "Read analytics reports and CSV exports"),
    "health.view": ("health", "View Dependency Health", "Read dependency health checks"),
    "alerts.view": ("alerts", "View Alerts", "Read the platform alert inbox"),
    "alerts.manage": ("alerts", "Manage Alerts", "Acknowledge and resolve alerts"),
    "incidents.view": ("incidents", "View Incidents", "Read platform incidents"),
    "incidents.manage": ("incidents", "Manage Incidents", "Create, update and resolve incidents"),
}


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.incidents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title VARCHAR(255) NOT NULL,
            severity VARCHAR(16) NOT NULL DEFAULT 'major'
                CHECK (severity IN ('critical', 'major', 'minor')),
            status VARCHAR(20) NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'investigating', 'resolved')),
            component VARCHAR(64),
            message TEXT,
            created_by_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            resolved_by_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            resolved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_incidents_status_created
        ON super_admin.incidents (status, created_at DESC)
        """
    )

    for code, (module, name, description) in PERM_META.items():
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.permissions (id, code, module, name, description)
                VALUES (gen_random_uuid(), :code, :module, :name, :description)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {"code": code, "module": module, "name": name, "description": description},
        )
    for role_code, codes in (
        ("owner", ALL_PERMS),
        ("super_admin", ALL_PERMS),
        ("operations", READ_ONLY_PERMS),
    ):
        for code in codes:
            op.get_bind().execute(
                sa.text(
                    """
                    INSERT INTO super_admin.role_permissions (role_id, permission_id)
                    SELECT r.id, p.id
                    FROM super_admin.roles r, super_admin.permissions p
                    WHERE r.code = :rc AND p.code = :pc
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"rc": role_code, "pc": code},
            )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS super_admin.incidents")
    for code in ALL_PERMS:
        op.execute(f"DELETE FROM super_admin.permissions WHERE code = '{code}'")
