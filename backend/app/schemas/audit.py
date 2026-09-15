from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional, Any
from uuid import UUID
from enum import Enum


class AuditAction(str, Enum):
    """Enumeration of all auditable actions."""
    # Booking operations
    BOOKING_CREATE = "booking.create"
    BOOKING_UPDATE = "booking.update"
    BOOKING_DELETE = "booking.delete"
    BOOKING_CANCEL = "booking.cancel"
    
    # Auth events
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_REGISTER = "auth.register"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_PASSWORD_RESET = "auth.password_reset"
    AUTH_PASSWORD_CHANGE = "auth.password_change"
    PROFILE_UPDATE = "account.profile_update"
    PLAN_CHANGE = "account.plan_change"
    
    # Config changes
    CONFIG_UPDATE = "config.update"
    
    # API key operations
    APIKEY_ROTATE = "apikey.rotate"
    
    # Organization operations
    ORG_CREATE = "org.create"
    ORG_UPDATE = "org.update"
    
    # Conversation handoff operations
    CONVERSATION_TAKEOVER = "conversation.takeover"
    CONVERSATION_RELEASE = "conversation.release"

    # Super Admin bridge operations (Phase 2)
    ORG_SUSPEND = "org.suspend"
    ORG_RESTORE = "org.restore"
    ORG_DELETE = "org.delete"
    USER_DISABLE = "user.disable"
    USER_ENABLE = "user.enable"

    # Impersonation operations (Phase 8, via bridge)
    IMPERSONATION_START = "impersonation.start"
    IMPERSONATION_END = "impersonation.end"

    # WhatsApp QR-gateway operations (Baileys transport)
    WHATSAPP_QR_CONNECT = "whatsapp.qr_connect"
    WHATSAPP_QR_DISCONNECT = "whatsapp.qr_disconnect"
    WHATSAPP_QR_BOOKING_TOGGLE = "whatsapp.qr_booking_toggle"


class AuditLogCreate(BaseModel):
    """Schema for creating audit log entries."""
    org_id: UUID
    actor_id: Optional[UUID] = None
    actor_email: Optional[str] = None
    action: AuditAction
    resource_type: str
    resource_id: Optional[str] = None
    old_values: Optional[dict[str, Any]] = None
    new_values: Optional[dict[str, Any]] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class AuditLogResponse(BaseModel):
    """Schema for audit log API responses."""
    id: UUID
    org_id: UUID
    actor_id: Optional[UUID]
    actor_email: Optional[str]
    action: str
    resource_type: str
    resource_id: Optional[str]
    old_values: Optional[dict[str, Any]]
    new_values: Optional[dict[str, Any]]
    ip_address: Optional[str]
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class AuditLogFilter(BaseModel):
    """Schema for filtering audit logs."""
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    action: Optional[str] = None
    actor_email: Optional[str] = None
    resource_type: Optional[str] = None
    page: int = 1
    page_size: int = 50
