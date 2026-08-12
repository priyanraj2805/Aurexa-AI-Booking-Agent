"""superadmin_phase14_otp_grants

Phase 14 - Org-admin consent OTP.

Replaces the emailed approve/reject links (Phase 11) with a short-lived
numeric OTP delivered to the org admin's registered email. The super admin
must enter the OTP before an impersonation session can be confirmed or a
conversation transcript can be peeked.

  - `super_admin.otp_grants` stores the OTP hash (never the plaintext),
    TTL, attempt budget and a `used_at` marker for single-use + the peek
    grant window.
  - The impersonation approval columns from Phase 11 are retired: the
    approval links are gone, approval state is expressed via
    `approver_email` / `approved_at` set by the OTP verification instead.

Revision ID: superadmin_phase14_otp_grants
Revises: superadmin_phase13_handshake_single_use
Create Date: 2026-08-10

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase14_otp_grants"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase13_handshake_single_use"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.otp_grants (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            purpose         VARCHAR(32) NOT NULL,
            admin_id        UUID NOT NULL REFERENCES super_admin.super_admins(id),
            org_id          UUID NOT NULL,
            recipient_email VARCHAR(255) NOT NULL,
            token_hash      VARCHAR(64) NOT NULL,
            expires_at      TIMESTAMPTZ NOT NULL,
            attempts_left   INTEGER NOT NULL DEFAULT 5,
            used_at         TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_otp_grants_active
        ON super_admin.otp_grants (admin_id, org_id, purpose, used_at)
        """
    )
    # Phase 11 approval columns are no longer needed — the OTP verification
    # replaces the emailed approve/reject links.
    op.execute(
        """
        ALTER TABLE super_admin.impersonations
            DROP COLUMN IF EXISTS approval_token_hash,
            DROP COLUMN IF EXISTS approval_expires_at
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE super_admin.impersonations
            ADD COLUMN IF NOT EXISTS approval_token_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS approval_expires_at TIMESTAMPTZ
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_otp_grants_active")
    op.execute("DROP TABLE IF EXISTS super_admin.otp_grants")
