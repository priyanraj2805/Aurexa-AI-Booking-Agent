"""reconcile ORM metadata with deployed schema (D-05)

Brings databases that were stamped at head without running every
intermediate migration in line with the current ORM models:

- drops legacy AdminPlus / handoff columns and their indexes/FKs
- renames legacy indexes to the names declared by the ORM models
- narrows org_configs text columns to the model lengths
  (live data already fits: 103/512, 21/2048, 9/32)
- replaces the legacy payment_attempts unique constraint with the
  model-declared unique index (uniqueness is preserved)

Every operation is guarded so the migration is idempotent on fresh
databases (where earlier migrations already applied these effects)
and on stale databases (where they were skipped).

Revision ID: reconcile_orm_schema
Revises: whatsapp_outbound_tracking
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "reconcile_orm_schema"
down_revision: Union[str, Sequence[str], None] = "whatsapp_outbound_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(bind, name: str) -> bool:
    return (
        bind.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
            {"n": name},
        ).scalar()
        is not None
    )


def _column_exists(bind, table: str, column: str) -> bool:
    return (
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
        is not None
    )


def _constraint_exists(bind, name: str) -> bool:
    return (
        bind.execute(
            sa.text("SELECT 1 FROM pg_constraint WHERE conname = :n"),
            {"n": name},
        ).scalar()
        is not None
    )


def _column_char_info(bind, table: str, column: str):
    return bind.execute(
        sa.text(
            "SELECT data_type, character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).fetchone()


def _drop_index(bind, name: str, table: str) -> None:
    if _index_exists(bind, name):
        op.drop_index(name, table_name=table)


def _create_index(bind, name: str, table: str, columns, **kwargs) -> None:
    if not _index_exists(bind, name):
        op.create_index(name, table, columns, **kwargs)


def upgrade() -> None:
    bind = op.get_bind()

    # ── bookings: legacy index names -> ORM index names ──
    _drop_index(bind, "ix_bookings_provider", "bookings")
    _drop_index(bind, "ix_bookings_razorpay_order", "bookings")
    _drop_index(bind, "ix_bookings_unit", "bookings")
    _create_index(bind, "ix_bookings_provider_id", "bookings", ["provider_id"])
    _create_index(bind, "ix_bookings_razorpay_order_id", "bookings", ["razorpay_order_id"])
    _create_index(bind, "ix_bookings_razorpay_payment_id", "bookings", ["razorpay_payment_id"])
    _create_index(bind, "ix_bookings_unit_id", "bookings", ["unit_id"])

    # ── chat_messages: legacy sender-type index -> ORM index ──
    _drop_index(bind, "ix_chat_messages_sender_type", "chat_messages")
    _create_index(bind, "ix_msg_sender_type", "chat_messages", ["sender_type"])

    # ── chat_sessions: drop obsolete handoff columns/indexes ──
    _drop_index(bind, "ix_chat_sessions_human_agent_active", "chat_sessions")
    _drop_index(bind, "ix_chat_sessions_status", "chat_sessions")
    _drop_index(bind, "ix_session_last_activity", "chat_sessions")
    if _column_exists(bind, "chat_sessions", "status"):
        op.drop_column("chat_sessions", "status")
    if _column_exists(bind, "chat_sessions", "handoff_requested_at"):
        op.drop_column("chat_sessions", "handoff_requested_at")

    # ── deny_rules: legacy index -> ORM index ──
    _drop_index(bind, "ix_deny_rules_org", "deny_rules")
    _create_index(bind, "ix_deny_rules_org_id", "deny_rules", ["org_id"])

    # ── login_codes: model declares no expiry index ──
    _drop_index(bind, "ix_login_codes_expiry", "login_codes")

    # ── org_configs: narrow text columns to model lengths ──
    info = _column_char_info(bind, "org_configs", "google_calendar_refresh_token")
    if info is not None and info[1] != 512:
        op.alter_column(
            "org_configs",
            "google_calendar_refresh_token",
            existing_type=sa.VARCHAR(1024),
            type_=sa.String(512),
            existing_nullable=True,
        )
    info = _column_char_info(bind, "org_configs", "website_url")
    if info is not None and info[0] == "text":
        op.alter_column(
            "org_configs",
            "website_url",
            existing_type=sa.Text(),
            type_=sa.String(2048),
            existing_nullable=True,
        )
    info = _column_char_info(bind, "org_configs", "crawl_status")
    if info is not None and info[1] != 32:
        op.alter_column(
            "org_configs",
            "crawl_status",
            existing_type=sa.VARCHAR(50),
            type_=sa.String(32),
            existing_nullable=True,
        )
    if _constraint_exists(bind, "uq_org_configs_widget_token"):
        op.drop_constraint("uq_org_configs_widget_token", "org_configs", type_="unique")

    # ── organizations: drop AdminPlus hierarchy remnants ──
    _drop_index(bind, "ix_organizations_onboarding_completed", "organizations")
    _drop_index(bind, "ix_organizations_parent_adminplus", "organizations")
    if _constraint_exists(bind, "fk_organizations_parent_adminplus"):
        op.drop_constraint(
            "fk_organizations_parent_adminplus", "organizations", type_="foreignkey"
        )
    if _column_exists(bind, "organizations", "kind"):
        op.drop_column("organizations", "kind")
    if _column_exists(bind, "organizations", "parent_adminplus_id"):
        op.drop_column("organizations", "parent_adminplus_id")

    # ── payment_attempts: legacy index/constraint -> ORM indexes ──
    _drop_index(bind, "ix_payment_attempt_route", "payment_attempts")
    if _constraint_exists(bind, "payment_attempts_razorpay_order_id_key"):
        op.drop_constraint(
            "payment_attempts_razorpay_order_id_key", "payment_attempts", type_="unique"
        )
    _create_index(bind, "ix_payment_attempts_org_id", "payment_attempts", ["org_id"])
    _create_index(
        bind,
        "ix_payment_attempts_razorpay_order_id",
        "payment_attempts",
        ["razorpay_order_id"],
        unique=True,
    )
    _create_index(
        bind, "ix_payment_attempts_razorpay_payment_id", "payment_attempts", ["razorpay_payment_id"]
    )
    _create_index(bind, "ix_payment_attempts_session_id", "payment_attempts", ["session_id"])

    # ── provider_fees / providers: legacy indexes -> ORM indexes ──
    _drop_index(bind, "ix_provider_fees_org", "provider_fees")
    _create_index(bind, "ix_provider_fees_org_id", "provider_fees", ["org_id"])
    _create_index(bind, "ix_provider_fees_provider_id", "provider_fees", ["provider_id"])
    _create_index(bind, "ix_provider_fees_unit_id", "provider_fees", ["unit_id"])
    _drop_index(bind, "ix_providers_org", "providers")
    _drop_index(bind, "ix_providers_org_user", "providers")
    _drop_index(bind, "ix_providers_user", "providers")
    _create_index(bind, "ix_providers_org_id", "providers", ["org_id"])
    _create_index(bind, "ix_providers_user_id", "providers", ["user_id"])

    # ── razorpay tables: legacy index -> ORM index ──
    _drop_index(bind, "ix_razorpay_transfers_order", "razorpay_transfers")
    _create_index(
        bind, "ix_razorpay_transfers_razorpay_order_id", "razorpay_transfers", ["razorpay_order_id"]
    )
    _create_index(
        bind, "ix_razorpay_webhook_events_org_id", "razorpay_webhook_events", ["org_id"]
    )

    # ── roles / service_units / unit_providers / user_roles ──
    _drop_index(bind, "ix_roles_org", "roles")
    _create_index(bind, "ix_roles_org_id", "roles", ["org_id"])
    _drop_index(bind, "ix_service_units_org", "service_units")
    _create_index(bind, "ix_service_units_org_id", "service_units", ["org_id"])
    _drop_index(bind, "ix_unit_providers_org", "unit_providers")
    _create_index(bind, "ix_unit_providers_org_id", "unit_providers", ["org_id"])
    _create_index(bind, "ix_unit_providers_provider_id", "unit_providers", ["provider_id"])
    _create_index(bind, "ix_unit_providers_unit_id", "unit_providers", ["unit_id"])
    _drop_index(bind, "ix_user_roles_user_org", "user_roles")


def downgrade() -> None:
    bind = op.get_bind()

    _drop_index(bind, "ix_user_roles_user_org", "user_roles")
    _create_index(bind, "ix_user_roles_user_org", "user_roles", ["user_id", "org_id"])
    _drop_index(bind, "ix_unit_providers_unit_id", "unit_providers")
    _drop_index(bind, "ix_unit_providers_provider_id", "unit_providers")
    _drop_index(bind, "ix_unit_providers_org_id", "unit_providers")
    _create_index(bind, "ix_unit_providers_org", "unit_providers", ["org_id"])
    _drop_index(bind, "ix_service_units_org_id", "service_units")
    _create_index(bind, "ix_service_units_org", "service_units", ["org_id"])
    _drop_index(bind, "ix_roles_org_id", "roles")
    _create_index(bind, "ix_roles_org", "roles", ["org_id"])
    _drop_index(bind, "ix_razorpay_webhook_events_org_id", "razorpay_webhook_events")
    _drop_index(bind, "ix_razorpay_transfers_razorpay_order_id", "razorpay_transfers")
    _create_index(bind, "ix_razorpay_transfers_order", "razorpay_transfers", ["razorpay_order_id"])
    _drop_index(bind, "ix_providers_user_id", "providers")
    _drop_index(bind, "ix_providers_org_id", "providers")
    _create_index(bind, "ix_providers_org", "providers", ["org_id"])
    _create_index(bind, "ix_providers_org_user", "providers", ["org_id", "user_id"])
    _create_index(bind, "ix_providers_user", "providers", ["user_id"])
    _drop_index(bind, "ix_provider_fees_unit_id", "provider_fees")
    _drop_index(bind, "ix_provider_fees_provider_id", "provider_fees")
    _drop_index(bind, "ix_provider_fees_org_id", "provider_fees")
    _create_index(bind, "ix_provider_fees_org", "provider_fees", ["org_id"])

    _drop_index(bind, "ix_payment_attempts_session_id", "payment_attempts")
    _drop_index(bind, "ix_payment_attempts_razorpay_payment_id", "payment_attempts")
    _drop_index(bind, "ix_payment_attempts_razorpay_order_id", "payment_attempts")
    _drop_index(bind, "ix_payment_attempts_org_id", "payment_attempts")
    if not _constraint_exists(bind, "payment_attempts_razorpay_order_id_key"):
        op.create_unique_constraint(
            "payment_attempts_razorpay_order_id_key", "payment_attempts", ["razorpay_order_id"]
        )
    _create_index(bind, "ix_payment_attempt_route", "payment_attempts", ["route_mode"])

    if not _column_exists(bind, "organizations", "parent_adminplus_id"):
        op.add_column(
            "organizations",
            sa.Column("parent_adminplus_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    if not _column_exists(bind, "organizations", "kind"):
        op.add_column(
            "organizations",
            sa.Column("kind", sa.String(20), server_default="TENANT", nullable=False),
        )
    if not _constraint_exists(bind, "fk_organizations_parent_adminplus"):
        op.create_foreign_key(
            "fk_organizations_parent_adminplus",
            "organizations",
            "organizations",
            ["parent_adminplus_id"],
            ["id"],
            ondelete="SET NULL",
        )
    _create_index(bind, "ix_organizations_parent_adminplus", "organizations", ["parent_adminplus_id"])
    _create_index(bind, "ix_organizations_onboarding_completed", "organizations", ["onboarding_completed"])

    if not _constraint_exists(bind, "uq_org_configs_widget_token"):
        op.create_unique_constraint(
            "uq_org_configs_widget_token", "org_configs", ["widget_token"]
        )
    op.alter_column(
        "org_configs",
        "crawl_status",
        existing_type=sa.String(32),
        type_=sa.String(50),
        existing_nullable=True,
    )
    op.alter_column(
        "org_configs",
        "website_url",
        existing_type=sa.String(2048),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "org_configs",
        "google_calendar_refresh_token",
        existing_type=sa.String(512),
        type_=sa.VARCHAR(1024),
        existing_nullable=True,
    )

    _create_index(bind, "ix_login_codes_expiry", "login_codes", ["expires_at"])
    _drop_index(bind, "ix_deny_rules_org_id", "deny_rules")
    _create_index(bind, "ix_deny_rules_org", "deny_rules", ["org_id"])

    if not _column_exists(bind, "chat_sessions", "handoff_requested_at"):
        op.add_column(
            "chat_sessions",
            sa.Column("handoff_requested_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _column_exists(bind, "chat_sessions", "status"):
        op.add_column(
            "chat_sessions",
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        )
    _create_index(bind, "ix_session_last_activity", "chat_sessions", ["last_activity_at"])
    _create_index(bind, "ix_chat_sessions_status", "chat_sessions", ["status"])
    _create_index(bind, "ix_chat_sessions_human_agent_active", "chat_sessions", ["human_agent_active"])

    _drop_index(bind, "ix_msg_sender_type", "chat_messages")
    _create_index(bind, "ix_chat_messages_sender_type", "chat_messages", ["sender_type"])

    _drop_index(bind, "ix_bookings_unit_id", "bookings")
    _drop_index(bind, "ix_bookings_razorpay_payment_id", "bookings")
    _drop_index(bind, "ix_bookings_razorpay_order_id", "bookings")
    _drop_index(bind, "ix_bookings_provider_id", "bookings")
    _create_index(bind, "ix_bookings_unit", "bookings", ["unit_id"])
    _create_index(bind, "ix_bookings_razorpay_order", "bookings", ["razorpay_order_id"])
    _create_index(bind, "ix_bookings_provider", "bookings", ["provider_id"])
