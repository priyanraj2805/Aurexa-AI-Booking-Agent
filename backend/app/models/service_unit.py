"""Service catalog: generic Units + Providers + Fees.

Generic vocabulary for ANY booking business (hospital is just an example):
  Unit     = department / branch / service lane  (e.g. Cardiology, Hair, Court 1)
  Provider = doctor / stylist / coach / consultant (any bookable person)
  UnitProvider = assignment of providers to units (many-to-many)
  ProviderFee  = fee master: per (unit, provider), provider-default, or
                 unit-default. At least one of unit_id / provider_id is set.

All rows are org-scoped. Deleting a unit/provider cascades its assignments;
fees cascade with their owner side; bookings keep history via SET NULL.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy import Column, ForeignKey, String, Boolean, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class ServiceUnit(Base):
    __tablename__ = "service_units"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(120), nullable=False)
    description = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default=sa.true())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Provider(Base):
    __tablename__ = "providers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    full_name = Column(String(255), nullable=False)
    role_label = Column(String(120), nullable=True)  # Doctor, Stylist, Coach…
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default=sa.true())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UnitProvider(Base):
    __tablename__ = "unit_providers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    unit_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_units.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_primary = Column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        sa.UniqueConstraint("unit_id", "provider_id", name="uq_unit_provider"),
    )


class ProviderFee(Base):
    __tablename__ = "provider_fees"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    unit_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_units.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    amount_paise = Column(Integer, nullable=False)
    currency = Column(String(10), nullable=False, default="INR", server_default="INR")
    label = Column(String(120), nullable=True)  # e.g. "First visit", "Follow-up"
    is_active = Column(Boolean, nullable=False, default=True, server_default=sa.true())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
