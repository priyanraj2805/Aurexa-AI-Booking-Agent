"""superadmin_phase15_consent_links

Phase 15 - Org-admin consent link (replaces the impersonation OTP).

The impersonation consent flow moves from a 6-digit OTP (Phase 14) back to
an emailed confirmation link (Phase 11 style), but with a 24-hour TTL and an
explicit in-browser "I Confirm" click by the org admin:

  - `super_admin.impersonations.approval_token_hash`: sha256 of the
    confirmation token (never the plaintext).
  - `super_admin.impersonations.approval_expires_at`: 24h from issue.

The conversation-peek OTP flow (Phase 14) is unchanged and still uses
`super_admin.otp_grants`.

Revision ID: superadmin_phase15_consent_links
Revises: superadmin_phase14_otp_grants
Create Date: 2026-08-10

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase15_consent_links"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase14_otp_grants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE super_admin.impersonations
            ADD COLUMN IF NOT EXISTS approval_token_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS approval_expires_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_impersonations_approval_token
        ON super_admin.impersonations (approval_token_hash)
        WHERE approval_token_hash IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_impersonations_approval_token")
    op.execute(
        """
        ALTER TABLE super_admin.impersonations
            DROP COLUMN IF EXISTS approval_token_hash,
            DROP COLUMN IF EXISTS approval_expires_at
        """
    )