"""superadmin_phase18_unmask_consent

Phase 18 - Separate org-admin consent for PII unmasking.

Previously a single "peek" approval unlocked BOTH masked transcript viewing
and full PII reveal (the latter also needing super-admin MFA + reason). This
makes consent purpose-aware: a `purpose` column on peek_requests separates
`peek` (view masked transcripts) from `unmask` (release customer PII), so the
org admin must approve each independently. Existing rows default to `peek`.

  - `super_admin.peek_requests.purpose`: VARCHAR(16) NOT NULL DEFAULT 'peek'.

Revision ID: superadmin_phase18_unmask_consent
Revises: tenant_broadcast_deliveries
Create Date: 2026-08-12

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase18_unmask_consent"
down_revision: Union[str, Sequence[str], None] = "tenant_broadcast_deliveries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE super_admin.peek_requests
        ADD COLUMN purpose VARCHAR(16) NOT NULL DEFAULT 'peek'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_peek_requests_grant_purpose
        ON super_admin.peek_requests (admin_id, org_id, purpose, status)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_peek_requests_grant_purpose")
    op.execute(
        "ALTER TABLE super_admin.peek_requests DROP COLUMN purpose"
    )
