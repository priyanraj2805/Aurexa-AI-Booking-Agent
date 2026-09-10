"""drop legacy technology-partner sub-merchant columns

Revision ID: d9e4f2a8
Revises: c8d3e1f5
Create Date: 2026-09-09

The Technology Partner sub-merchant flow was removed: booking payments are
created on the platform account and split via Razorpay Route. These columns
held per-org OAuth tokens and onboarding state that nothing reads anymore.
Appointment pricing columns (appointment_price_*) are intentionally kept —
Route order amounts come from them.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d9e4f2a8"
down_revision: Union[str, Sequence[str], None] = "c8d3e1f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_COLUMNS = [
    ("razorpay_submerchant_id", sa.String(length=64)),
    ("razorpay_public_token", sa.String(length=255)),
    ("razorpay_access_token_enc", sa.String(length=2048)),
    ("razorpay_refresh_token_enc", sa.String(length=2048)),
    ("razorpay_access_expires_at", postgresql.TIMESTAMP(timezone=True)),
    ("razorpay_onboarding_state", sa.String(length=64)),
    ("razorpay_status", sa.String(length=20)),
    ("razorpay_connected_at", postgresql.TIMESTAMP(timezone=True)),
    ("razorpay_activated_at", postgresql.TIMESTAMP(timezone=True)),
    ("razorpay_failure_reason", sa.String(length=500)),
]


def upgrade() -> None:
    for name, _ in LEGACY_COLUMNS:
        op.drop_column("org_configs", name)


def downgrade() -> None:
    for name, coltype in LEGACY_COLUMNS:
        op.add_column("org_configs", sa.Column(name, coltype, nullable=True))
