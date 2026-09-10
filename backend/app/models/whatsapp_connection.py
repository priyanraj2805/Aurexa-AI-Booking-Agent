"""WhatsApp QR-connection registry (Baileys transport).

One row per organization max (UNIQUE org_id). This table owns organization
ownership, connection metadata and status. Baileys authentication state lives
in the wa-gateway service (volume wa_auth/<connection_id>/), referenced from
here — never in OrgConfig and never in logs.
"""

import uuid
from sqlalchemy import Column, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class WhatsAppConnectionStatus:
    """Connection lifecycle. QR pairing sessions start at PENDING_QR."""

    PENDING_QR = "PENDING_QR"  # socket started, waiting for admin scan
    CONNECTED = "CONNECTED"  # authenticated + socket open
    DISCONNECTED = "DISCONNECTED"  # socket down, auth kept (auto-reconnect)
    LOGGED_OUT = "LOGGED_OUT"  # unlinked from phone, auth wiped, needs re-scan

    ALL = frozenset({PENDING_QR, CONNECTED, DISCONNECTED, LOGGED_OUT})


class WhatsAppConnection(Base):
    __tablename__ = "whatsapp_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # E.164 digits set once Baileys reports the linked identity (own JID).
    phone_number = Column(String(32), nullable=True)
    display_name = Column(String(255), nullable=True)

    status = Column(
        String(20),
        nullable=False,
        default=WhatsAppConnectionStatus.PENDING_QR,
        server_default="PENDING_QR",
    )

    connected_at = Column(DateTime(timezone=True), nullable=True)
    disconnected_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        # One WhatsApp session per organization max — the core isolation rule.
        UniqueConstraint("org_id", name="uq_whatsapp_conn_org"),
        Index("ix_whatsapp_conn_org", "org_id"),
        Index("ix_whatsapp_conn_status", "status"),
    )
