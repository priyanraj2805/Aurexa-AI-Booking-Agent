import uuid
from sqlalchemy import Column, String, DateTime, Index, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.db.base import Base


class SystemAlert(Base):
    __tablename__ = "system_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    component = Column(String(32), nullable=False)  # llm | email | database
    severity = Column(String(16), nullable=False)  # warning | critical
    code = Column(String(64), nullable=False, index=True)
    dedupe_key = Column(String(160), nullable=True, index=True)
    message = Column(String(1000), nullable=False)
    context = Column(JSONB, nullable=True)
    request_id = Column(String(128), nullable=True)
    trace_id = Column(String(128), nullable=True)
    occurrence_count = Column(Integer, nullable=False, default=1, server_default="1")
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by_user_id = Column(UUID(as_uuid=True), nullable=True)
    acknowledgement_note = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_system_alert_component_severity_created", "component", "severity", "created_at"),
        Index("ix_system_alert_active_dedupe", "dedupe_key", "resolved_at"),
    )

