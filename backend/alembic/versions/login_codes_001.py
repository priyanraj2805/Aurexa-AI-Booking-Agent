"""Single-use login codes for URL-visible handoff (OAuth code-style).

Revision ID: login_codes_001
Revises: rbac_001
Create Date: 2026-09-21

login_codes stores SHA256 hashes (never raw codes) of short-lived,
single-use codes: login validates credentials → issues code (in URL) →
exchange swaps it for the HttpOnly session cookie. Raw codes appear only
in the URL for seconds and are stripped after exchange.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "login_codes_001"
down_revision: Union[str, Sequence[str], None] = "rbac_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "login_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="TENANT"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash", name="uq_login_codes_hash"),
    )
    op.create_index("ix_login_codes_expiry", "login_codes", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_login_codes_expiry", table_name="login_codes")
    op.drop_table("login_codes")
