import uuid
from sqlalchemy import Column, String, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.db.base import Base


class AuditLog(Base):
    """
    Immutable audit log for compliance tracking.
    Records all sensitive operations with before/after state.
    
    Design notes:
    - No foreign keys to allow logging even if referenced entities are deleted
    - JSONB for old_values/new_values to capture arbitrary state
    - Indexed on org_id, created_at, action for common query patterns
    """
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Tenant isolation - all queries MUST filter by org_id
    org_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    # Actor info (who performed the action)
    actor_id = Column(UUID(as_uuid=True), nullable=True)  # Null for system actions
    actor_email = Column(String(255), nullable=True)  # Denormalized for query convenience
    
    # Action details
    action = Column(String(50), nullable=False, index=True)  # e.g., "booking.create", "auth.login"
    resource_type = Column(String(50), nullable=False)  # e.g., "booking", "user", "config"
    resource_id = Column(String(255), nullable=True)  # UUID or identifier of affected resource
    
    # State change (JSONB for flexibility)
    old_values = Column(JSONB, nullable=True)  # State before change (null for creates)
    new_values = Column(JSONB, nullable=True)  # State after change (null for deletes)
    
    # Request context
    ip_address = Column(String(45), nullable=True)  # IPv6 max length
    user_agent = Column(Text, nullable=True)
    
    # Timestamp (immutable - no updated_at)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        # Composite index for common dashboard query: filter by org + date range
        Index('ix_audit_logs_org_created', 'org_id', 'created_at'),
        # Composite index for filtering by org + action type
        Index('ix_audit_logs_org_action', 'org_id', 'action'),
    )
