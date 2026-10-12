"""org mode foundation: individual vs organization

Revision ID: org_mode_001
Revises: 16a_online_meeting_fields, 6f1a2b3c4d5e, chat_message_external_id
Create Date: 2026-10-06

Merges the 3 current heads into one and adds additive-only columns:

  - organizations.type VARCHAR(20) default 'individual'
    ('individual' = single doctor shop, 'organization' = hospital mall)
    Existing rows backfilled to 'individual' so old flow untouched.
  - providers.timezone / slot_duration_minutes / booking_window_days
    (per-doctor timetable override, NULL = use org default)
  - schedule_days.provider_id NULL (NULL = org template, set = doctor override)
  - schedule_exceptions.provider_id NULL (same rule)
  - time_slots.provider_id + unit_id NULL
    Drops old uq_timeslot_org_start (org,start) which blocked two doctors
    sharing the same time. Replaced with partial uniques:
      * individual slots (provider NULL): unique(org,start)
      * provider slots: unique(org,provider,start)
  - chat_sessions.unit_id + provider_id NULL (remembers dept+doctor in chat)

All nullable/additive — individual flow keeps working.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "org_mode_001"
down_revision: Union[str, Sequence[str], None] = (
    "16a_online_meeting_fields",
    "6f1a2b3c4d5e",
    "chat_message_external_id",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. organizations.type
    op.add_column(
        "organizations",
        sa.Column(
            "type",
            sa.String(20),
            nullable=False,
            server_default="individual",
        ),
    )
    op.execute(
        "UPDATE organizations SET type = 'individual' WHERE type IS NULL OR type = ''"
    )
    op.create_index("ix_organizations_type", "organizations", ["type"])

    # 2. providers per-doctor overrides
    op.add_column(
        "providers",
        sa.Column("timezone", sa.String(64), nullable=True),
    )
    op.add_column(
        "providers",
        sa.Column("slot_duration_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "providers",
        sa.Column("booking_window_days", sa.Integer(), nullable=True),
    )

    # 3. schedule_days.provider_id (NULL = org template)
    op.add_column(
        "schedule_days",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_schedule_days_provider",
        "schedule_days",
        "providers",
        ["provider_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_schedule_days_provider", "schedule_days", ["org_id", "provider_id"]
    )

    # 4. schedule_exceptions.provider_id
    op.add_column(
        "schedule_exceptions",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_schedule_exceptions_provider",
        "schedule_exceptions",
        "providers",
        ["provider_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_schedule_exceptions_provider",
        "schedule_exceptions",
        ["org_id", "provider_id"],
    )

    # 5. time_slots.provider_id + unit_id
    op.add_column(
        "time_slots",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "time_slots",
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_timeslots_provider",
        "time_slots",
        "providers",
        ["provider_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_timeslots_unit",
        "time_slots",
        "service_units",
        ["unit_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_timeslots_provider", "time_slots", ["provider_id"])
    # Drop old global unique that blocked two doctors at same time
    # (IF EXISTS: never wrap op.* in try/except — it aborts the transaction)
    op.execute("ALTER TABLE time_slots DROP CONSTRAINT IF EXISTS uq_timeslot_org_start")
    # Partial uniques: individual (provider NULL) vs per-doctor
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_timeslot_org_start_individual
        ON time_slots (org_id, start_time)
        WHERE provider_id IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_timeslot_org_provider_start
        ON time_slots (org_id, provider_id, start_time)
        WHERE provider_id IS NOT NULL
        """
    )

    # 6. chat_sessions remembers dept + doctor
    op.add_column(
        "chat_sessions",
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sessions_unit",
        "chat_sessions",
        "service_units",
        ["unit_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_sessions_provider",
        "chat_sessions",
        "providers",
        ["provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_sessions_unit", "chat_sessions", ["unit_id"])
    op.create_index("ix_sessions_provider", "chat_sessions", ["provider_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_provider", table_name="chat_sessions")
    op.drop_index("ix_sessions_unit", table_name="chat_sessions")
    op.drop_constraint("fk_sessions_provider", "chat_sessions", type_="foreignkey")
    op.drop_constraint("fk_sessions_unit", "chat_sessions", type_="foreignkey")
    op.drop_column("chat_sessions", "provider_id")
    op.drop_column("chat_sessions", "unit_id")
    op.execute("DROP INDEX IF EXISTS uq_timeslot_org_provider_start")
    op.execute("DROP INDEX IF EXISTS uq_timeslot_org_start_individual")
    op.drop_index("ix_timeslots_provider", table_name="time_slots")
    op.drop_constraint("fk_timeslots_unit", "time_slots", type_="foreignkey")
    op.drop_constraint("fk_timeslots_provider", "time_slots", type_="foreignkey")
    op.drop_column("time_slots", "unit_id")
    op.drop_column("time_slots", "provider_id")
    op.create_unique_constraint(
        "uq_timeslot_org_start", "time_slots", ["org_id", "start_time"]
    )
    op.drop_index(
        "ix_schedule_exceptions_provider", table_name="schedule_exceptions"
    )
    op.drop_constraint(
        "fk_schedule_exceptions_provider",
        "schedule_exceptions",
        type_="foreignkey",
    )
    op.drop_column("schedule_exceptions", "provider_id")
    op.drop_index("ix_schedule_days_provider", table_name="schedule_days")
    op.drop_constraint(
        "fk_schedule_days_provider", "schedule_days", type_="foreignkey"
    )
    op.drop_column("schedule_days", "provider_id")
    op.drop_column("providers", "booking_window_days")
    op.drop_column("providers", "slot_duration_minutes")
    op.drop_column("providers", "timezone")
    op.drop_index("ix_organizations_type", table_name="organizations")
    op.drop_column("organizations", "type")
