"""superadmin_phase19_password_reset

Forgot-password support for the super-admin control plane.

A short-lived numeric OTP is emailed to the admin's registered address.
The admin proves possession of the code before setting a new password.

  - `super_admin.password_reset_tokens` stores the OTP hash (never the
    plaintext), TTL, attempt budget and a single-use marker, keyed by the
    admin's email (the admin is not authenticated at this point).

Revision ID: superadmin_phase19_password_reset
Revises: tenant_auth_otp_records
Create Date: 2026-08-14

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase19_password_reset"
down_revision: Union[str, Sequence[str], None] = "tenant_auth_otp_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.password_reset_tokens (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email         VARCHAR(255) NOT NULL,
            token_hash    VARCHAR(64) NOT NULL,
            expires_at    TIMESTAMPTZ NOT NULL,
            attempts_left INTEGER NOT NULL DEFAULT 5,
            used_at       TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_active
        ON super_admin.password_reset_tokens (email, used_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_password_reset_tokens_active")
    op.execute("DROP TABLE IF EXISTS super_admin.password_reset_tokens")