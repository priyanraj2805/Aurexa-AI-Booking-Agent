"""superadmin_phase4_approvals

Phase 4 — Two-person approval workflow for destructive actions
(org delete first; refunds, impersonation and feature flags later).

  - super_admin.approvals: request → approve/reject by a SECOND admin.
    - request_type    e.g. org.delete
    - payload         JSONB with the bridge call parameters
    - status          PENDING → APPROVED → EXECUTED | REJECTED | EXPIRED
    - expires_at      24h from creation; approval attempts on expired
                      requests are rejected.
    - second-person rule is enforced in code (requested_by != reviewer).

Revision ID: superadmin_phase4_approvals
Revises: superadmin_phase2_usage_events
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase4_approvals"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase2_usage_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.approvals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            request_type VARCHAR(50) NOT NULL,
            resource_type VARCHAR(100) NOT NULL,
            resource_id VARCHAR(255),
            payload JSONB NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            requested_by_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            requested_by_email VARCHAR(255),
            reviewed_by_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            reviewed_by_email VARCHAR(255),
            review_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            reviewed_at TIMESTAMPTZ,
            executed_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_approvals_status
        ON super_admin.approvals (status, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_approvals_resource
        ON super_admin.approvals (resource_type, resource_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS super_admin.approvals")
