import uuid
from sqlalchemy import Boolean, Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class OrgNotification(Base):
    """Org-scoped in-app notification (e.g. booking created / cancelled).

    Surfaced in the admin-panel bell icon. Rows are addressed to the whole
    org (any admin user of the org sees them); read state is tracked inline.
    """

    __tablename__ = "org_notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    kind = Column(String(40), nullable=False)  # booking_created | booking_cancelled
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)
    resource_type = Column(String(60), nullable=True)
    resource_id = Column(String(64), nullable=True)
    is_read = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_org_notifications_inbox",
            "org_id",
            "is_read",
            "created_at",
        ),
    )
