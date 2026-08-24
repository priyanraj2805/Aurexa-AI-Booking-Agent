"""tenant_org_notifications

Org-scoped in-app notifications for the admin panel bell icon.
First consumers: booking created / booking cancelled events emitted by the
booking engine and admin dashboard.

Revision ID: tenant_org_notifications
Revises: e27a4d5fc4a0
Create Date: 2026-08-21

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "tenant_org_notifications"
down_revision: Union[str, Sequence[str], None] = "e27a4d5fc4a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id UUID NOT NULL,
            kind VARCHAR(40) NOT NULL,
            title VARCHAR(255) NOT NULL,
            body TEXT,
            resource_type VARCHAR(60),
            resource_id VARCHAR(64),
            is_read BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            read_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_org_notifications_org_id
        ON org_notifications (org_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_org_notifications_inbox
        ON org_notifications (org_id, is_read, created_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_org_notifications_inbox")
    op.execute("DROP INDEX IF EXISTS ix_org_notifications_org_id")
    op.execute("DROP TABLE IF EXISTS org_notifications")
