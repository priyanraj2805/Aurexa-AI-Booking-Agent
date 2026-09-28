import uuid

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class BroadcastDelivery(Base):
    """Tenant inbox row for a super-admin broadcast."""

    __tablename__ = "broadcast_deliveries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    broadcast_id = Column(
        UUID(as_uuid=True),
        ForeignKey("super_admin.broadcast_notifications.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("broadcast_id", "org_id"),
        Index("ix_broadcast_deliveries_inbox", "org_id", sa.desc("created_at")),
    )


class BroadcastRead(Base):
    """Per-user read state for a broadcast delivery."""

    __tablename__ = "broadcast_reads"

    delivery_id = Column(
        UUID(as_uuid=True),
        ForeignKey("broadcast_deliveries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    read_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
