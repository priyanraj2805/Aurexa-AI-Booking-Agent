"""superadmin_phase1_audit_log

Adds the tamper-proof audit trail for Super Admin actions:
  - super_admin.audit_logs table with hash-chained records
  - UPDATE/DELETE triggers that hard-block any tampering

Revision ID: superadmin_phase1_audit_log
Revises: superadmin_phase0_foundations
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase1_audit_log"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase0_foundations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            actor_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            actor_email VARCHAR(255),
            action VARCHAR(100) NOT NULL,
            resource_type VARCHAR(100) NOT NULL,
            resource_id VARCHAR(255),
            old_values JSONB,
            new_values JSONB,
            ip_address VARCHAR(45),
            user_agent TEXT,
            previous_hash VARCHAR(64),
            record_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_actor "
        "ON super_admin.audit_logs (actor_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_action "
        "ON super_admin.audit_logs (action, created_at)"
    )

    # ── Tamper-proofing: reject any UPDATE or DELETE ───────────────────────
    op.execute(
        """
        CREATE OR REPLACE FUNCTION super_admin.audit_logs_no_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs are append-only; mutation rejected';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS audit_logs_no_update ON super_admin.audit_logs
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_update
        BEFORE UPDATE ON super_admin.audit_logs
        FOR EACH ROW EXECUTE FUNCTION super_admin.audit_logs_no_mutation()
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS audit_logs_no_delete ON super_admin.audit_logs
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_delete
        BEFORE DELETE ON super_admin.audit_logs
        FOR EACH ROW EXECUTE FUNCTION super_admin.audit_logs_no_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_delete ON super_admin.audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update ON super_admin.audit_logs")
    op.execute(
        "DROP FUNCTION IF EXISTS super_admin.audit_logs_no_mutation()"
    )
    op.execute("DROP TABLE IF EXISTS super_admin.audit_logs")
