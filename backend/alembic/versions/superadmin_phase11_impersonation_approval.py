"""superadmin_phase11_impersonation_approval

Phase 11 — Org-approved impersonation.

Imprenetion now requires the impersonated org admin to approve via a
one-time email link BEFORE the 30-min token is minted:

  - `status` becomes a full lifecycle: 'pending' -> 'approved'/'rejected'
    -> 'active' -> 'ended' (retro 'active' rows keep the old values).
  - `token_jti` is now NULLABLE: no session is created (and no token is
    issued) until the super admin confirms after org approval.
  - `approval_token_hash` stores a sha256 of the one-time approval secret
    sent in the email (raw secret is never persisted).
  - `approver_email`, `approved_at`, `rejected_at`, `approval_expires_at`
    record who/when and bound the approval window (24h, mirroring
    super_admin.approvals TTL).

Revision ID: superadmin_phase11_impersonation_approval
Revises: superadmin_phase10_session_jti
Create Date: 2026-08-07

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase11_impersonation_approval"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase10_session_jti"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE super_admin.impersonations
            ALTER COLUMN token_jti DROP NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE super_admin.impersonations
            ALTER COLUMN expires_at DROP NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE super_admin.impersonations
            ADD COLUMN IF NOT EXISTS approval_token_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS approver_email VARCHAR(255),
            ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS approval_expires_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_impersonations_pending
        ON super_admin.impersonations (admin_id, status)
        WHERE status = 'pending'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE super_admin.impersonations
            DROP COLUMN IF EXISTS approval_expires_at,
            DROP COLUMN IF EXISTS rejected_at,
            DROP COLUMN IF EXISTS approved_at,
            DROP COLUMN IF EXISTS approver_email,
            DROP COLUMN IF EXISTS approval_token_hash,
            ALTER COLUMN token_jti SET NOT NULL,
            ALTER COLUMN expires_at SET NOT NULL
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_impersonations_pending")