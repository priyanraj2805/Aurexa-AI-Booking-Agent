"""add meeting provider and link fields

Revision ID: 16a_online_meeting_fields
Revises: 15a_timezone_booking_snapshots
Create Date: 2026-03-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "16a_online_meeting_fields"
down_revision: Union[str, Sequence[str], None] = "15a_timezone_booking_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # OrgConfig: Meeting provider connection flags
    op.add_column(
        "org_configs",
        sa.Column(
            "google_meet_connected",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "org_configs",
        sa.Column(
            "microsoft_teams_connected",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "org_configs",
        sa.Column(
            "zoom_connected",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # OrgConfig: Zoom OAuth tokens
    op.add_column(
        "org_configs",
        sa.Column("zoom_refresh_token", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "org_configs",
        sa.Column("zoom_access_token", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "org_configs",
        sa.Column("zoom_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # OrgConfig: Active meeting provider (distinct from active_calendar_provider)
    op.add_column(
        "org_configs",
        sa.Column(
            "active_meeting_provider",
            sa.String(length=32),
            nullable=False,
            server_default="NONE",
        ),
    )

    # OrgConfig: Meeting provider audit trail
    op.add_column(
        "org_configs",
        sa.Column(
            "meeting_provider_updated_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "org_configs",
        sa.Column("meeting_provider_updated_by", sa.String(length=255), nullable=True),
    )

    # Booking: Meeting link snapshot fields
    op.add_column(
        "bookings",
        sa.Column("meeting_type_snapshot", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column("meeting_link_url", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column("meeting_link_provider", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column(
            "meeting_link_status",
            sa.String(length=20),
            nullable=False,
            server_default="NOT_REQUIRED",
        ),
    )
    op.add_column(
        "bookings",
        sa.Column("meeting_link_external_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column("meeting_link_error_code", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    # Booking: Drop meeting link fields
    op.drop_column("bookings", "meeting_link_error_code")
    op.drop_column("bookings", "meeting_link_external_id")
    op.drop_column("bookings", "meeting_link_status")
    op.drop_column("bookings", "meeting_link_provider")
    op.drop_column("bookings", "meeting_link_url")
    op.drop_column("bookings", "meeting_type_snapshot")

    # OrgConfig: Drop meeting provider audit trail
    op.drop_column("org_configs", "meeting_provider_updated_by")
    op.drop_column("org_configs", "meeting_provider_updated_at")

    # OrgConfig: Drop active meeting provider
    op.drop_column("org_configs", "active_meeting_provider")

    # OrgConfig: Drop Zoom OAuth tokens
    op.drop_column("org_configs", "zoom_token_expires_at")
    op.drop_column("org_configs", "zoom_access_token")
    op.drop_column("org_configs", "zoom_refresh_token")

    # OrgConfig: Drop meeting provider connection flags
    op.drop_column("org_configs", "zoom_connected")
    op.drop_column("org_configs", "microsoft_teams_connected")
    op.drop_column("org_configs", "google_meet_connected")
