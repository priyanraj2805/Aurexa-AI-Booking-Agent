"""add phone OTP (Plivo SMS) support

Revision ID: c3d4e5f6
Revises: b2c3d4e5
Create Date: 2026-08-31

Adds:
- org_configs.require_phone_otp BOOL DEFAULT false
- chat_sessions.phone_otp_verified BOOL DEFAULT false
- otp_records.channel VARCHAR(10) DEFAULT 'email' + unique(session_id, channel)
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent — safe to run even if columns already exist (manual SQL applied)
    op.execute("ALTER TABLE org_configs ADD COLUMN IF NOT EXISTS require_phone_otp BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS phone_otp_verified BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE otp_records ADD COLUMN IF NOT EXISTS channel VARCHAR(10) DEFAULT 'email'")
    op.execute("UPDATE otp_records SET channel='email' WHERE channel IS NULL")
    op.execute("ALTER TABLE otp_records ALTER COLUMN channel SET DEFAULT 'email'")
    op.execute("ALTER TABLE otp_records ALTER COLUMN channel SET NOT NULL")
    # Migrate unique constraint to (session_id, channel)
    op.execute("ALTER TABLE otp_records DROP CONSTRAINT IF EXISTS otp_records_session_id_key")
    op.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='uq_otp_session_channel') THEN ALTER TABLE otp_records ADD CONSTRAINT uq_otp_session_channel UNIQUE (session_id, channel); END IF; END $$;")


def downgrade() -> None:
    try:
        op.drop_constraint("uq_otp_session_channel", "otp_records", type_="unique")
    except Exception:
        pass
    op.drop_column("otp_records", "channel")
    op.drop_column("chat_sessions", "phone_otp_verified")
    op.drop_column("org_configs", "require_phone_otp")
