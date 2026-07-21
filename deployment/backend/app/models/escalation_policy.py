import uuid
from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class EscalationPolicy(Base):
    __tablename__ = "escalation_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EscalationRoutingTarget(Base):
    __tablename__ = "escalation_routing_targets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("escalation_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_type = Column(String(32), nullable=False)
    target_value = Column(String(255), nullable=False)
    order_index = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("policy_id", "order_index", name="uq_target_policy_order"),)


class EscalationRotationWindow(Base):
    __tablename__ = "escalation_rotation_windows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("escalation_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_of_week = Column(Integer, nullable=False)
    start_hour_utc = Column(Integer, nullable=False)
    end_hour_utc = Column(Integer, nullable=False)
    order_index = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("policy_id", "order_index", name="uq_rotation_policy_order"),)
