"""superadmin_phase16_inapp_notifications

Phase 16 - In-app notifications for super admins.

Stores per-admin notifications surfaced in the super-admin dashboard (bell
icon). First consumer: org-admin consent confirmations for impersonation
requests, so the requesting super admin is alerted when the org admin
clicked "I Confirm".

  - `super_admin.inapp_notifications`: one row per notification, addressed
    to a super admin; read state tracked inline.

Revision ID: superadmin_phase16_inapp_notifications
Revises: superadmin_phase15_consent_links
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase16_inapp_notifications"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase15_consent_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.inapp_notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            admin_id UUID NOT NULL REFERENCES super_admin.super_admins (id) ON DELETE CASCADE,
            kind VARCHAR(40) NOT NULL,
            title VARCHAR(255) NOT NULL,
            body TEXT,
            resource_type VARCHAR(60),
            resource_id VARCHAR(64),
            is_read BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            read_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_inapp_notifications_inbox
        ON super_admin.inapp_notifications (admin_id, is_read, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inapp_notifications_inbox")
    op.execute("DROP TABLE IF EXISTS super_admin.inapp_notifications")
