"""superadmin_phase10_session_jti

Phase 10 — Refresh-token session binding.

Stores the JWT `jti` claim on each admin session so the refresh endpoint can
verify the token's identifier against the session that issued it. Previously
the code compared `session.id` (a UUID primary key) against the random `jti`
string — which can never match — so every refresh returned 401 and sessions
could not be rotated.

Revision ID: superadmin_phase10_session_jti
Revises: superadmin_phase9_pii_unmask
Create Date: 2026-08-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase10_session_jti"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase9_pii_unmask"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE super_admin.admin_sessions "
        "ADD COLUMN IF NOT EXISTS jti VARCHAR(64)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE super_admin.admin_sessions DROP COLUMN IF EXISTS jti"
    )
