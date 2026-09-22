"""service catalog: units, providers, assignments, fees + booking linkage

Revision ID: units_providers_001
Revises: adminplus_hierarchy
Create Date: 2026-09-21

Generic catalog for ANY booking business (hospital is just an example):
  - service_units (departments / branches / lanes)
  - providers (doctors / stylists / coaches)
  - unit_providers (many-to-many assignments, uq_unit_provider)
  - provider_fees (per unit+provider / provider-default / unit-default)
  - bookings.unit_id / bookings.provider_id (nullable, SET NULL — history kept)

No backfill needed: all new tables start empty; booking columns nullable.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "units_providers_001"
down_revision: Union[str, Sequence[str], None] = "remove_adminplus_hierarchy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_units_org", "service_units", ["org_id"])

    op.create_table(
        "providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("role_label", sa.String(120), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_providers_org", "providers", ["org_id"])

    op.create_table(
        "unit_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["unit_id"], ["service_units.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("unit_id", "provider_id", name="uq_unit_provider"),
    )
    op.create_index("ix_unit_providers_org", "unit_providers", ["org_id"])

    op.create_table(
        "provider_fees",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="INR"),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["unit_id"], ["service_units.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_fees_org", "provider_fees", ["org_id"])

    op.add_column(
        "bookings",
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_bookings_unit", "bookings", "service_units",
        ["unit_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_bookings_provider", "bookings", "providers",
        ["provider_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_bookings_unit", "bookings", ["unit_id"])
    op.create_index("ix_bookings_provider", "bookings", ["provider_id"])


def downgrade() -> None:
    op.drop_index("ix_bookings_provider", table_name="bookings")
    op.drop_index("ix_bookings_unit", table_name="bookings")
    op.drop_constraint("fk_bookings_provider", "bookings", type_="foreignkey")
    op.drop_constraint("fk_bookings_unit", "bookings", type_="foreignkey")
    op.drop_column("bookings", "provider_id")
    op.drop_column("bookings", "unit_id")
    op.drop_index("ix_provider_fees_org", table_name="provider_fees")
    op.drop_table("provider_fees")
    op.drop_index("ix_unit_providers_org", table_name="unit_providers")
    op.drop_table("unit_providers")
    op.drop_index("ix_providers_org", table_name="providers")
    op.drop_table("providers")
    op.drop_index("ix_service_units_org", table_name="service_units")
    op.drop_table("service_units")
