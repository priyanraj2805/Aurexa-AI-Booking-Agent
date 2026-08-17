"""tenant_auth_otp_records

Stores email OTPs for admin-panel authentication flows.

The booking-flow OTP table (public.otp_records) is tied to chat sessions,
so it cannot hold auth OTPs for account creation (no session exists yet)
or login. This table keys OTPs by (email, purpose) so a code is scoped to
exactly one flow and one inbox.

  - `public.auth_otp_records`: one row per (email, purpose) — register/login
    verification codes, hashed, with expiry, resend and attempt caps.

Revision ID: tenant_auth_otp_records
Revises: tenant_broadcast_deliveries
Create Date: 2026-08-13

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "tenant_auth_otp_records"
down_revision: Union[str, Sequence[str], None] = "tenant_broadcast_deliveries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.auth_otp_records (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(255) NOT NULL,
            purpose VARCHAR(20) NOT NULL,
            code_hash VARCHAR(255) NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            resend_count INTEGER NOT NULL DEFAULT 0,
            last_sent_at TIMESTAMPTZ,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_auth_otp_email_purpose UNIQUE (email, purpose)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_auth_otp_records_email
        ON public.auth_otp_records (email)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_auth_otp_records_email")
    op.execute("DROP TABLE IF EXISTS public.auth_otp_records")
