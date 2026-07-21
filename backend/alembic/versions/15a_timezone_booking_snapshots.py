"""add timezone snapshot fields to bookings

Revision ID: 15a_timezone_booking_snapshots
Revises: 14a_meeting_policy_fields
Create Date: 2026-03-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "15a_timezone_booking_snapshots"
down_revision: Union[str, Sequence[str], None] = "14a_meeting_policy_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bookings", sa.Column("user_timezone", sa.String(length=64), nullable=True))
    op.add_column(
        "bookings", sa.Column("user_slot_start_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "bookings", sa.Column("user_slot_end_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("bookings", sa.Column("timezone_source", sa.String(length=20), nullable=True))
    op.add_column(
        "bookings", sa.Column("org_timezone_snapshot", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "bookings", sa.Column("org_slot_start_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "bookings", sa.Column("org_slot_end_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("bookings", "org_slot_end_at")
    op.drop_column("bookings", "org_slot_start_at")
    op.drop_column("bookings", "org_timezone_snapshot")
    op.drop_column("bookings", "timezone_source")
    op.drop_column("bookings", "user_slot_end_at")
    op.drop_column("bookings", "user_slot_start_at")
    op.drop_column("bookings", "user_timezone")

