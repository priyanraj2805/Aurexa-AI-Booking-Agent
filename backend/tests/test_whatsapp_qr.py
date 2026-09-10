"""WhatsApp QR pairing endpoints (P5). Gateway calls are faked; isolation enforced."""
import uuid

from sqlalchemy.future import select

from app.core.config import settings
from app.models.whatsapp_connection import (
    WhatsAppConnection,
    WhatsAppConnectionStatus,
)


class FakeGateway:
    """Stands in for app.core.wa_gateway (monkeypatched per test)."""

    def __init__(self):
        self.calls = []
        self.mode = "ok"  # ok | down | unknown | no_qr

    async def start_session(self, connection_id):
        self.calls.append(("start", connection_id))
        if self.mode == "down":
            from app.core.wa_gateway import WaGatewayError
            raise WaGatewayError("WhatsApp gateway is unreachable")
        return {"connectionId": connection_id, "status": "awaiting_qr"}

    async def get_qr(self, connection_id):
        self.calls.append(("qr", connection_id))
        if self.mode == "down":
            from app.core.wa_gateway import WaGatewayError
            raise WaGatewayError("WhatsApp gateway is unreachable")
        if self.mode == "no_qr":
            return None
        return {"connectionId": connection_id, "qrDataUrl": "data:image/png;base64,QR",
                "issuedAt": 1, "expiresAt": 2}

    async def get_session_status(self, connection_id):
        self.calls.append(("status", connection_id))
        if self.mode == "down":
            from app.core.wa_gateway import WaGatewayError
            raise WaGatewayError("WhatsApp gateway is unreachable")
        if self.mode == "unknown":
            return None
        return {"connectionId": connection_id, "status": "connected",
                "phoneNumber": "919521433306", "displayName": "Test Biz",
                "lastSeenAt": "2026-09-10T00:00:00+00:00"}

    async def logout_session(self, connection_id):
        self.calls.append(("logout", connection_id))
        if self.mode == "down":
            from app.core.wa_gateway import WaGatewayError
            raise WaGatewayError("WhatsApp gateway is unreachable")
        return {"connectionId": connection_id, "status": "logged_out"}

    async def reconnect_session(self, connection_id):
        self.calls.append(("reconnect", connection_id))
        if self.mode == "down":
            from app.core.wa_gateway import WaGatewayError
            raise WaGatewayError("WhatsApp gateway is unreachable")
        if self.mode == "unknown":
            return None
        return {"connectionId": connection_id, "status": "reconnecting"}


def _patch(monkeypatch, fake):
    import app.api.v1.endpoints.whatsapp_qr as ep
    monkeypatch.setattr(ep.wa_gateway, "start_session", fake.start_session)
    monkeypatch.setattr(ep.wa_gateway, "get_qr", fake.get_qr)
    monkeypatch.setattr(ep.wa_gateway, "get_session_status", fake.get_session_status)
    monkeypatch.setattr(ep.wa_gateway, "logout_session", fake.logout_session)
    monkeypatch.setattr(ep.wa_gateway, "reconnect_session", fake.reconnect_session)


async def _row(db_session, org_id):
    return (
        await db_session.execute(
            select(WhatsAppConnection).where(WhatsAppConnection.org_id == org_id)
        )
    ).scalars().first()


async def test_connect_start_creates_row_and_calls_gateway(client, registered_user, monkeypatch, db_session):
    fake = FakeGateway()
    _patch(monkeypatch, fake)
    r = await client.post("/api/v1/dashboard/whatsapp-qr/connect/start",
                          headers=registered_user["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "PENDING_QR"
    row = await _row(db_session, registered_user["user"]["org_id"])
    assert row is not None
    assert ("start", str(row.id)) in fake.calls


async def test_connect_start_idempotent_no_duplicate_row(client, registered_user, monkeypatch, db_session):
    fake = FakeGateway()
    _patch(monkeypatch, fake)
    h = registered_user["headers"]
    r1 = await client.post("/api/v1/dashboard/whatsapp-qr/connect/start", headers=h)
    r2 = await client.post("/api/v1/dashboard/whatsapp-qr/connect/start", headers=h)
    assert r1.status_code == 200 and r2.status_code == 200
    rows = (
        await db_session.execute(
            select(WhatsAppConnection).where(
                WhatsAppConnection.org_id == registered_user["user"]["org_id"])
        )
    ).scalars().all()
    assert len(rows) == 1  # UNIQUE(org_id) holds under double-click


async def test_connect_start_rejected_when_already_connected(client, registered_user, monkeypatch, db_session):
    fake = FakeGateway()
    _patch(monkeypatch, fake)
    db_session.add(WhatsAppConnection(
        org_id=registered_user["user"]["org_id"],
        status=WhatsAppConnectionStatus.CONNECTED,
        phone_number="919521433306"))
    await db_session.commit()
    r = await client.post("/api/v1/dashboard/whatsapp-qr/connect/start",
                          headers=registered_user["headers"])
    assert r.status_code == 409


async def test_qr_passthrough_and_no_fresh_qr(client, registered_user, monkeypatch, db_session):
    fake = FakeGateway()
    _patch(monkeypatch, fake)
    h = registered_user["headers"]
    # No row yet -> 404, never touches the gateway.
    r0 = await client.get("/api/v1/dashboard/whatsapp-qr/qr", headers=h)
    assert r0.status_code == 404
    assert ("qr", "__never__") not in [c[1] for c in fake.calls]

    await client.post("/api/v1/dashboard/whatsapp-qr/connect/start", headers=h)
    r1 = await client.get("/api/v1/dashboard/whatsapp-qr/qr", headers=h)
    assert r1.status_code == 200
    assert r1.json()["qr_data_url"].startswith("data:image/png")

    fake.mode = "no_qr"
    r2 = await client.get("/api/v1/dashboard/whatsapp-qr/qr", headers=h)
    assert r2.status_code == 404
    assert r2.json()["detail"] == "NO_FRESH_QR"


async def test_status_merges_gateway_identity_and_state(client, registered_user, monkeypatch, db_session):
    fake = FakeGateway()
    _patch(monkeypatch, fake)
    h = registered_user["headers"]
    # No row -> NOT_CONNECTED (200, so the UI has an initial state).
    r0 = await client.get("/api/v1/dashboard/whatsapp-qr/status", headers=h)
    assert r0.status_code == 200 and r0.json()["status"] == "NOT_CONNECTED"

    await client.post("/api/v1/dashboard/whatsapp-qr/connect/start", headers=h)
    r1 = await client.get("/api/v1/dashboard/whatsapp-qr/status", headers=h)
    assert r1.status_code == 200
    body = r1.json()
    assert body["status"] == "CONNECTED"
    assert body["phone_number"] == "919521433306"
    row = await _row(db_session, registered_user["user"]["org_id"])
    assert row.status == "CONNECTED"  # DB synced from socket truth


async def test_status_reconciles_unknown_gateway_session(client, registered_user, monkeypatch, db_session):
    fake = FakeGateway()
    fake.mode = "unknown"
    _patch(monkeypatch, fake)
    h = registered_user["headers"]
    db_session.add(WhatsAppConnection(
        org_id=registered_user["user"]["org_id"],
        status=WhatsAppConnectionStatus.CONNECTED))
    await db_session.commit()
    # Gateway forgot the socket -> backend re-drives start (resume from creds).
    r = await client.get("/api/v1/dashboard/whatsapp-qr/status", headers=h)
    assert r.status_code == 200
    assert any(c[0] == "start" for c in fake.calls)


async def test_disconnect_marks_logged_out(client, registered_user, monkeypatch, db_session):
    fake = FakeGateway()
    _patch(monkeypatch, fake)
    h = registered_user["headers"]
    r0 = await client.post("/api/v1/dashboard/whatsapp-qr/disconnect", headers=h)
    assert r0.status_code == 404  # nothing to disconnect

    await client.post("/api/v1/dashboard/whatsapp-qr/connect/start", headers=h)
    r1 = await client.post("/api/v1/dashboard/whatsapp-qr/disconnect", headers=h)
    assert r1.status_code == 200
    assert r1.json()["status"] == "LOGGED_OUT"
    row = await _row(db_session, registered_user["user"]["org_id"])
    assert row.status == "LOGGED_OUT" and row.disconnected_at is not None


async def test_reconnect_needs_scan_when_logged_out(client, registered_user, monkeypatch, db_session):
    fake = FakeGateway()
    _patch(monkeypatch, fake)
    h = registered_user["headers"]
    db_session.add(WhatsAppConnection(
        org_id=registered_user["user"]["org_id"],
        status=WhatsAppConnectionStatus.LOGGED_OUT))
    await db_session.commit()
    r = await client.post("/api/v1/dashboard/whatsapp-qr/reconnect", headers=h)
    assert r.status_code == 409


async def test_reconnect_drives_gateway(client, registered_user, monkeypatch, db_session):
    fake = FakeGateway()
    _patch(monkeypatch, fake)
    h = registered_user["headers"]
    db_session.add(WhatsAppConnection(
        org_id=registered_user["user"]["org_id"],
        status=WhatsAppConnectionStatus.DISCONNECTED))
    await db_session.commit()
    r = await client.post("/api/v1/dashboard/whatsapp-qr/reconnect", headers=h)
    assert r.status_code == 200
    assert any(c[0] == "reconnect" for c in fake.calls)


async def test_cross_org_isolation(client, registered_user, monkeypatch, db_session):
    """Org B's connection row is invisible to org A's user in every response."""
    fake = FakeGateway()
    _patch(monkeypatch, fake)
    other_org = uuid.uuid4()
    db_session.add(WhatsAppConnection(
        org_id=other_org, status=WhatsAppConnectionStatus.CONNECTED,
        phone_number="911111111111", display_name="Other Org"))
    await db_session.commit()
    h = registered_user["headers"]
    for path, method in [
        ("/api/v1/dashboard/whatsapp-qr/status", "GET"),
        ("/api/v1/dashboard/whatsapp-qr/qr", "GET"),
    ]:
        r = await client.request(method, path, headers=h)
        assert "911111111111" not in r.text
        assert "Other Org" not in r.text
    # And this org still has no row of its own.
    assert (await _row(db_session, registered_user["user"]["org_id"])) is None


async def test_gateway_down_maps_to_502(client, registered_user, monkeypatch):
    fake = FakeGateway()
    fake.mode = "down"
    _patch(monkeypatch, fake)
    h = registered_user["headers"]
    r = await client.post("/api/v1/dashboard/whatsapp-qr/connect/start", headers=h)
    assert r.status_code == 502


# ── Internal QR inbound (P7) ─────────────────────────────────────────────

class TestQrInbound:
    def _headers(self, monkeypatch):
        monkeypatch.setattr(settings, "WA_GATEWAY_SERVICE_KEY", "test-gw-key")
        return {"X-Gateway-Key": "test-gw-key"}

    def _payload(self, connection_id, **over):
        body = {
            "connectionId": str(connection_id),
            "messageId": "wamid.HBgM123",
            "from": "919521433306",
            "chatJid": "919521433306@s.whatsapp.net",
            "text": "Hi, I need an appointment",
        }
        body.update(over)
        return body

    async def test_rejects_bad_service_key(self, client, db_session):
        r = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload("11111111-2222-4333-8444-555555555555"),
            headers={"X-Gateway-Key": "wrong"},
        )
        assert r.status_code == 403

    async def test_unknown_connection_404(self, client, monkeypatch):
        r = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload("22222222-2222-4222-8222-222222222222"),
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 404

    async def test_happy_path_runs_engine_and_returns_reply(
        self, client, registered_user, monkeypatch, db_session
    ):
        from types import SimpleNamespace
        from sqlalchemy.future import select
        from app.engine import runtime_router
        from app.models.config import OrgConfig
        from app.models.whatsapp_connection import WhatsAppConnection

        seen = {}

        async def fake_route(*, db, org, org_config, chat_session, message_text, background_tasks=None):
            seen["org_id"] = str(org.id)
            seen["text"] = message_text
            seen["session_channel"] = chat_session.channel
            assert org_config is not None  # real OrgConfig, never None
            return SimpleNamespace(widget_directive="TEXT_INPUT", message="Hello!", field_meta=None)

        monkeypatch.setattr(runtime_router, "route_whatsapp_message", fake_route)
        org_id = registered_user["user"]["org_id"]
        cfg = (
            await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
        ).scalars().first()
        assert cfg is not None  # registration creates it
        row = WhatsAppConnection(org_id=org_id, status="CONNECTED", phone_number="919521433306")
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)

        r = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload(row.id),
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok"
        assert body["reply"]["type"] == "text"
        assert body["reply"]["body"] == "Hello!"
        assert seen["org_id"] == str(org_id)
        assert seen["text"] == "Hi, I need an appointment"
        assert seen["session_channel"] == "whatsapp"

        # Same message id again -> duplicate (engine runs once).
        r2 = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload(row.id),
            headers=self._headers(monkeypatch),
        )
        assert r2.json()["status"] == "duplicate"

    async def test_inactive_org_ignored(self, client, registered_user, monkeypatch, db_session):
        from app.models.organization import Organization
        from app.models.whatsapp_connection import WhatsAppConnection
        from sqlalchemy.future import select

        org_id = registered_user["user"]["org_id"]
        org = (
            await db_session.execute(select(Organization).where(Organization.id == org_id))
        ).scalars().first()
        org.is_active = False
        row = WhatsAppConnection(org_id=org_id, status="CONNECTED")
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        r = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload(row.id, messageId="wamid.HBgM999"),
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"

    async def test_require_payment_prompt_reaches_customer_unchanged(
        self, client, registered_user, monkeypatch, db_session
    ):
        """P9 parity: a REQUIRE_PAYMENT engine directive flows to the WhatsApp
        customer verbatim (amount included). Payment itself is still completed
        via the widget/checkout flow, exactly as on the old Cloud transport."""
        from types import SimpleNamespace
        from sqlalchemy.future import select
        from app.engine import runtime_router
        from app.models.config import OrgConfig
        from app.models.whatsapp_connection import WhatsAppConnection

        async def fake_route(*, db, org, org_config, chat_session, message_text, background_tasks=None):
            return SimpleNamespace(
                widget_directive="REQUIRE_PAYMENT",
                message="Your appointment total is ₹500.00. Please pay to confirm your booking.",
                field_meta={"amount_paise": 50000, "currency": "INR"},
            )

        monkeypatch.setattr(runtime_router, "route_whatsapp_message", fake_route)
        org_id = registered_user["user"]["org_id"]
        assert (
            await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
        ).scalars().first() is not None
        row = WhatsAppConnection(org_id=org_id, status="CONNECTED")
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)

        r = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload(row.id, messageId="wamid.HBgMpay1"),
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok"
        assert body["reply"]["type"] == "text"
        assert "₹500.00" in body["reply"]["body"]
