"""superadmin_phase17_peek_consent

Phase 17 - Org-admin consent LINKS for conversation transcript peek.

Replaces the OTP flow (Phase 14) for opening read-only transcripts: the super
admin issues a peek request, the org admin receives an emailed confirmation
link, and transcript content unlocks only after the org admin clicks
"I Confirm" (status 'approved').

  - `super_admin.peek_requests`: one row per request, storing only the sha256
    of the approval token (never plaintext), single-use and TTL'd exactly like
    the Phase 15 impersonation consent links.

Revision ID: superadmin_phase17_peek_consent
Revises: superadmin_phase16_inapp_notifications
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase17_peek_consent"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase16_inapp_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.peek_requests (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            admin_id UUID NOT NULL REFERENCES super_admin.super_admins (id) ON DELETE CASCADE,
            org_id UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            org_name VARCHAR(255),
            session_id UUID,
            channel_user_id VARCHAR(255),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            recipient_email VARCHAR(255),
            approver_email VARCHAR(255),
            approved_at TIMESTAMPTZ,
            approval_token_hash VARCHAR(64),
            approval_expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_peek_requests_approval_token
        ON super_admin.peek_requests (approval_token_hash)
        WHERE approval_token_hash IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_peek_requests_status
        ON super_admin.peek_requests (admin_id, status)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_peek_requests_grant
        ON super_admin.peek_requests (admin_id, org_id, status)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_peek_requests_grant")
    op.execute("DROP INDEX IF EXISTS ix_peek_requests_status")
    op.execute("DROP INDEX IF EXISTS ix_peek_requests_approval_token")
    op.execute("DROP TABLE IF EXISTS super_admin.peek_requests")