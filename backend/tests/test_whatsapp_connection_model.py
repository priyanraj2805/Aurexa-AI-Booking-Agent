"""WhatsApp QR-connection model tests (P1: registry only, no sockets)."""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from app.models.organization import Organization
from app.models.whatsapp_connection import (
    WhatsAppConnection,
    WhatsAppConnectionStatus,
)


async def _make_org(db_session):
    org = Organization(
        name="WA Conn Org",
        api_key=f"ds_waconn_{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    return org


async def test_create_defaults_to_pending_qr(db_session):
    org = await _make_org(db_session)
    row = WhatsAppConnection(org_id=org.id)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.status == WhatsAppConnectionStatus.PENDING_QR
    assert row.phone_number is None
    assert row.connected_at is None


async def test_one_connection_per_org(db_session):
    org = await _make_org(db_session)
    db_session.add(WhatsAppConnection(org_id=org.id, status="CONNECTED"))
    await db_session.commit()
    # Second row for the same org must violate UNIQUE(org_id) — the core
    # multi-tenant isolation rule (one session per organization max).
    db_session.add(WhatsAppConnection(org_id=org.id, status="PENDING_QR"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_status_lifecycle_values(db_session):
    assert WhatsAppConnectionStatus.ALL == frozenset(
        {"PENDING_QR", "CONNECTED", "DISCONNECTED", "LOGGED_OUT"}
    )
    org = await _make_org(db_session)
    row = WhatsAppConnection(
        org_id=org.id,
        phone_number="919521433306",
        display_name="Test Business",
        status=WhatsAppConnectionStatus.CONNECTED,
    )
    db_session.add(row)
    await db_session.commit()
    fetched = (
        await db_session.execute(
            select(WhatsAppConnection).where(WhatsAppConnection.org_id == org.id)
        )
    ).scalars().first()
    assert fetched is not None
    assert fetched.phone_number == "919521433306"
    assert fetched.status == "CONNECTED"
