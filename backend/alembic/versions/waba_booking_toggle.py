"""add whatsapp_booking_enabled toggle to whatsapp_connections

Revision ID: waba_booking_toggle
Revises: a3f8c1d9e2b4
Create Date: 2026-09-12

Admin-facing kill switch for WhatsApp booking autoreplies. Effective booking
is (toggle ON) AND (meeting_mode in ONLINE_ONLY/HYBRID); OFFLINE_ONLY always
disables. Defaults to false so connecting alone never enables replies.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "waba_booking_toggle"
down_revision: Union[str, Sequence[str], None] = "a3f8c1d9e2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_connections",
        sa.Column(
            "whatsapp_booking_enabled",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_connections", "whatsapp_booking_enabled")
