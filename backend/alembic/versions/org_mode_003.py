"""org mode phase 3 fix: per-doctor schedule days coexist with org template

Revision ID: org_mode_003
Revises: org_mode_002

Old unique uq_schedule_org_day (org_id, day_of_week) allowed only one row
per weekday per org, so provider overrides could never be inserted.
Replace with partial uniques:
  - org template: unique(org, day) WHERE provider_id IS NULL
  - provider override: unique(org, provider, day) WHERE provider NOT NULL
"""

from typing import Sequence, Union

from alembic import op

revision: str = "org_mode_003"
down_revision: Union[str, Sequence[str], None] = "org_mode_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: never wrap op.* in try/except — a failed statement aborts the
    # whole migration transaction and every later statement fails with
    # "current transaction is aborted" (this crash-looped the backend).
    # Use IF EXISTS / IF NOT EXISTS so the migration is safely re-runnable.
    op.execute("ALTER TABLE schedule_days DROP CONSTRAINT IF EXISTS uq_schedule_org_day")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_org_day_template
        ON schedule_days (org_id, day_of_week)
        WHERE provider_id IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_org_provider_day
        ON schedule_days (org_id, provider_id, day_of_week)
        WHERE provider_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_schedule_org_provider_day")
    op.execute("DROP INDEX IF EXISTS uq_schedule_org_day_template")
    # Restoring the old constraint fails if provider overrides exist —
    # only run when the table is override-free.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM schedule_days WHERE provider_id IS NOT NULL) THEN
                ALTER TABLE schedule_days
                ADD CONSTRAINT uq_schedule_org_day UNIQUE (org_id, day_of_week);
            END IF;
        END
        $$;
        """
    )
