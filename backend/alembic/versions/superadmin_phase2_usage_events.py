"""superadmin_phase2_usage_events

Phase 2 — Tenant data access layer foundation:
  - super_admin.usage_events: monthly-partitioned telemetry counters
    (bookings, AI tokens, API calls per org), written by the MAIN backend
    only when TELEMETRY_ENABLED=true (gate stays false until GA).
  - Read-only role access to the super_admin schema (for analytics reads).
  - DEFAULT PRIVILEGES on public schema so future tenant tables stay
    SELECT-only readable by the super_admin_readonly role.

Revision ID: superadmin_phase2_usage_events
Revises: superadmin_phase1_audit_log
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase2_usage_events"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase1_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS super_admin")

    # ── 1. Monthly-partitioned usage counters ──────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.usage_events (
            event_month DATE NOT NULL,
            org_id UUID NOT NULL,
            metric VARCHAR(64) NOT NULL,
            count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (event_month, org_id, metric)
        ) PARTITION BY RANGE (event_month)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_usage_events_org_month
        ON super_admin.usage_events (org_id, event_month DESC)
        """
    )

    # ── 2. Auto-create partition for the month being written ──────────────
    op.execute(
        """
        CREATE OR REPLACE FUNCTION super_admin.ensure_usage_partition()
        RETURNS TRIGGER AS $$
        DECLARE
            part_name TEXT;
            part_start DATE;
            part_end DATE;
        BEGIN
            part_start := date_trunc('month', NEW.event_month)::date;
            part_end := (part_start + INTERVAL '1 month')::date;
            part_name := 'usage_events_' || to_char(part_start, 'YYYY_MM');
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS super_admin.%I '
                'PARTITION OF super_admin.usage_events '
                'FOR VALUES FROM (%L) TO (%L)',
                part_name, part_start, part_end
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_usage_ensure_partition ON super_admin.usage_events
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_usage_ensure_partition
        BEFORE INSERT ON super_admin.usage_events
        FOR EACH ROW EXECUTE FUNCTION super_admin.ensure_usage_partition()
        """
    )
    # Create the current month's partition directly (DO block: SELECT on a
    # function created in the same transaction breaks asyncpg's prepared
    # statement type resolution).
    op.execute(
        """
        DO $$
        DECLARE
            part_name TEXT;
            part_start DATE;
            part_end DATE;
        BEGIN
            part_start := date_trunc('month', now())::date;
            part_end := (part_start + INTERVAL '1 month')::date;
            part_name := 'usage_events_' || to_char(part_start, 'YYYY_MM');
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS super_admin.%I '
                'PARTITION OF super_admin.usage_events '
                'FOR VALUES FROM (%L) TO (%L)',
                part_name, part_start, part_end
            );
        END;
        $$ LANGUAGE plpgsql
        """
    )

    # ── 3. Read-only role: SELECT on the super_admin schema too ────────────
    # (usage_events reads for Phase 6 analytics; no writes possible: the role
    #  has default_transaction_read_only=on from Phase 0.)
    op.execute(
        """
        GRANT USAGE ON SCHEMA super_admin TO super_admin_readonly
        """
    )
    op.execute(
        """
        GRANT SELECT ON ALL TABLES IN SCHEMA super_admin TO super_admin_readonly
        """
    )

    # ── 4. Future-proofing: new public tables remain read-only for the role ──
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES
        TO super_admin_readonly
        """
    )
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA super_admin
        GRANT SELECT ON TABLES TO super_admin_readonly
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_usage_ensure_partition ON super_admin.usage_events"
    )
    op.execute("DROP TABLE IF EXISTS super_admin.usage_events CASCADE")
