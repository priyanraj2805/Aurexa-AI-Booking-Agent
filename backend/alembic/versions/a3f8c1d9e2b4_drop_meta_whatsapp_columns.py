"""drop Meta Cloud API WhatsApp columns from org_configs

Revision ID: a3f8c1d9e2b4
Revises: f7e2a9c4d1b8
Create Date: 2026-09-10

Cutover to the QR/Baileys transport: per-org Meta credentials, verify tokens
and the enabled flag are dead state. No business data is touched — only these
five transport columns. whatsapp_connections (added in f7e2a9c4d1b8) is the
new source of truth for WhatsApp connectivity.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a3f8c1d9e2b4"
down_revision: Union[str, Sequence[str], None] = "f7e2a9c4d1b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_COLUMNS = [
    ("whatsapp_enabled", sa.Boolean()),
    ("whatsapp_phone_number_id", sa.String(length=255)),
    ("whatsapp_business_account_id", sa.String(length=255)),
    ("whatsapp_verify_token", sa.String(length=255)),
    ("whatsapp_api_token", sa.String(length=512)),
]


def upgrade() -> None:
    for name, _ in LEGACY_COLUMNS:
        op.drop_column("org_configs", name)


def downgrade() -> None:
    for name, coltype in LEGACY_COLUMNS:
        op.add_column("org_configs", sa.Column(name, coltype, nullable=True))
