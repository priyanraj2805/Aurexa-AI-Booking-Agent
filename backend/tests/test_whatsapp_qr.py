"""WhatsApp QR pairing endpoints (P5). Gateway calls are faked; isolation enforced."""
import uuid

from sqlalchemy.future import select

from app.core.config import settings
from app.models.whatsapp_connection import (
    WhatsAppConnection,
    WhatsAppConnectionStatus,
)
from app.models.session import ChatSession
from app.models.message import ChatMessage


class FakeGateway:
    """Stands in for app.core.wa_gateway (monkeypatched per test)."""

    def __init__(self):
        self.calls = []
        self.mode = "ok"  # ok | down | unknown | no_qr
        self.gw_status = "connected"  # live socket truth for get_session_status

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
        return {"connectionId": connection_id, "status": self.gw_status,
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
    fake.gw_status = "reconnecting"  # live socket actually down
    _patch(monkeypatch, fake)
    h = registered_user["headers"]
    db_session.add(WhatsAppConnection(
        org_id=registered_user["user"]["org_id"],
        status=WhatsAppConnectionStatus.DISCONNECTED))
    await db_session.commit()
    r = await client.post("/api/v1/dashboard/whatsapp-qr/reconnect", headers=h)
    assert r.status_code == 200
    assert any(c[0] == "reconnect" for c in fake.calls)


async def test_reconnect_noop_when_socket_already_connected(
    client, registered_user, monkeypatch, db_session
):
    """A healthy socket must never be force-restarted: killing it triggers
    a WhatsApp `replaced` conflict storm and drops in-flight replies."""
    fake = FakeGateway()  # gw_status defaults to "connected"
    _patch(monkeypatch, fake)
    h = registered_user["headers"]
    db_session.add(WhatsAppConnection(
        org_id=registered_user["user"]["org_id"],
        status=WhatsAppConnectionStatus.DISCONNECTED))  # stale row
    await db_session.commit()
    r = await client.post("/api/v1/dashboard/whatsapp-qr/reconnect", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "CONNECTED"
    assert not any(c[0] == "reconnect" for c in fake.calls)
    row = await _row(db_session, registered_user["user"]["org_id"])
    assert row.status == "CONNECTED"  # stale row healed without restart


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

        async def fake_route(*, db, org, org_config, chat_session, message_text,
                             background_tasks=None, transcript_user_text=None,
                             inbound_message_id=None):
            seen["org_id"] = str(org.id)
            seen["text"] = message_text
            seen["transcript_text"] = transcript_user_text
            seen["inbound_id"] = inbound_message_id
            seen["session_channel"] = chat_session.channel
            assert org_config is not None  # real OrgConfig, never None
            return SimpleNamespace(widget_directive="TEXT_INPUT", message="Hello!", field_meta=None)

        monkeypatch.setattr(runtime_router, "route_whatsapp_message", fake_route)
        org_id = registered_user["user"]["org_id"]
        cfg = (
            await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
        ).scalars().first()
        assert cfg is not None  # registration creates it
        from app.models.config import MeetingMode

        cfg.meeting_mode = MeetingMode.HYBRID
        row = WhatsAppConnection(
            org_id=org_id,
            status="CONNECTED",
            phone_number="919521433306",
            whatsapp_booking_enabled=True,
        )
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
        # The inbound handler must hand the engine both the untouched text and
        # the provider id, so the transcript can be traced back to this message.
        assert seen["transcript_text"] == "Hi, I need an appointment"
        assert seen["inbound_id"] == self._payload(row.id)["messageId"]

        # Same message id again -> duplicate (engine runs once).
        r2 = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload(row.id),
            headers=self._headers(monkeypatch),
        )
        assert r2.json()["status"] == "duplicate"

    async def test_pending_confirm_does_not_frame_unrelated_reply(
        self, client, registered_user, monkeypatch, db_session
    ):
        """A stale PENDING CONFIRM_BOOKING directive must not staple
        "Please confirm your booking … Reply CONFIRM" onto an unrelated
        engine reply (edit prompt, lookup answer, greeting)."""
        from types import SimpleNamespace
        from sqlalchemy.future import select
        from app.crud import chat as crud_chat
        from app.engine import runtime_router
        from app.engine.booking_directive.service import (
            create_directive as _create_dir,
        )
        from app.models.config import OrgConfig

        async def fake_route(*, db, org, org_config, chat_session, message_text,
                             background_tasks=None, transcript_user_text=None,
                             inbound_message_id=None):
            return SimpleNamespace(
                widget_directive="TEXT_INPUT",
                message="Our team can help with bookings.",
                field_meta={},
            )

        monkeypatch.setattr(runtime_router, "route_whatsapp_message", fake_route)
        org_id = registered_user["user"]["org_id"]
        cfg = (
            await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
        ).scalars().first()
        assert cfg is not None
        from app.models.config import MeetingMode

        cfg.meeting_mode = MeetingMode.HYBRID
        row = WhatsAppConnection(
            org_id=org_id,
            status="CONNECTED",
            phone_number="919521433306",
            whatsapp_booking_enabled=True,
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)

        session = await crud_chat.get_or_create_channel_session(
            db_session, str(org_id), "whatsapp", "919521433306"
        )
        created = await _create_dir(
            db_session,
            org_id=org_id,
            customer_id="919521433306",
            session_id=session.session_id,
            type="CONFIRM_BOOKING",
            title="Please confirm your booking",
            description="",
            options=[
                {"id": "confirm_booking", "label": "CONFIRM", "value": "confirm"},
                {"id": "change_booking", "label": "CHANGE", "value": "change"},
            ],
            required=True,
            expires_in_seconds=600,
            meta={},
        )
        collected = dict(session.collected_data or {})
        collected["active_directive_id"] = str(created.id)
        session.collected_data = collected
        await crud_chat.update_chat_session(db_session, str(session.session_id), session)

        r = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload(row.id, messageId="wamid.FRAME1", text="What are your hours?"),
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 200, r.text
        body = r.json()["reply"]["body"]
        assert body == "Our team can help with bookings."
        assert "Please confirm your booking" not in body
        assert "Reply CONFIRM" not in body

    async def test_confirm_step_keeps_directive_framing(
        self, client, registered_user, monkeypatch, db_session
    ):
        """When the engine really returns the confirmation step, the
        pending CONFIRM_BOOKING directive still frames the reply."""
        from types import SimpleNamespace
        from sqlalchemy.future import select
        from app.crud import chat as crud_chat
        from app.engine import runtime_router
        from app.engine.booking_directive.service import (
            create_directive as _create_dir,
        )
        from app.models.config import OrgConfig

        async def fake_route(*, db, org, org_config, chat_session, message_text,
                             background_tasks=None, transcript_user_text=None,
                             inbound_message_id=None):
            return SimpleNamespace(
                widget_directive="REQUEST_CONFIRMATION",
                message="Here is your booking summary.",
                field_meta={},
            )

        monkeypatch.setattr(runtime_router, "route_whatsapp_message", fake_route)
        org_id = registered_user["user"]["org_id"]
        cfg = (
            await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
        ).scalars().first()
        assert cfg is not None
        from app.models.config import MeetingMode

        cfg.meeting_mode = MeetingMode.HYBRID
        row = WhatsAppConnection(
            org_id=org_id,
            status="CONNECTED",
            phone_number="919521433306",
            whatsapp_booking_enabled=True,
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)

        session = await crud_chat.get_or_create_channel_session(
            db_session, str(org_id), "whatsapp", "919521433306"
        )
        created = await _create_dir(
            db_session,
            org_id=org_id,
            customer_id="919521433306",
            session_id=session.session_id,
            type="CONFIRM_BOOKING",
            title="Please confirm your booking",
            description="",
            options=[
                {"id": "confirm_booking", "label": "CONFIRM", "value": "confirm"},
                {"id": "change_booking", "label": "CHANGE", "value": "change"},
            ],
            required=True,
            expires_in_seconds=600,
            meta={},
        )
        collected = dict(session.collected_data or {})
        collected["active_directive_id"] = str(created.id)
        session.collected_data = collected
        await crud_chat.update_chat_session(db_session, str(session.session_id), session)

        r = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload(row.id, messageId="wamid.FRAME2", text="I see"),
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 200, r.text
        body = r.json()["reply"]["body"]
        assert "Please confirm your booking" in body
        assert "Here is your booking summary." in body
        assert "Reply CONFIRM to confirm or CHANGE to modify." in body

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

        async def fake_route(*, db, org, org_config, chat_session, message_text,
                             background_tasks=None, transcript_user_text=None,
                             inbound_message_id=None):
            return SimpleNamespace(
                widget_directive="REQUIRE_PAYMENT",
                message="Your appointment total is ₹500.00. Please pay to confirm your booking.",
                field_meta={"amount_paise": 50000, "currency": "INR"},
            )

        monkeypatch.setattr(runtime_router, "route_whatsapp_message", fake_route)
        org_id = registered_user["user"]["org_id"]
        from app.models.config import MeetingMode

        cfg = (
            await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
        ).scalars().first()
        assert cfg is not None  # registration creates it
        cfg.meeting_mode = MeetingMode.HYBRID
        row = WhatsAppConnection(
            org_id=org_id, status="CONNECTED", whatsapp_booking_enabled=True
        )
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


# ── Gateway lifecycle events (P-event push) ────────────────────────────

class TestQrEvents:
    def _headers(self, monkeypatch):
        monkeypatch.setattr(settings, "WA_GATEWAY_SERVICE_KEY", "test-gw-key")
        return {"X-Gateway-Key": "test-gw-key"}

    async def _row_with_status(self, db_session, org_id, status):
        row = WhatsAppConnection(org_id=org_id, status=status)
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    async def test_rejects_bad_service_key(self, client):
        r = await client.post(
            "/api/v1/internal/whatsapp-qr/events",
            json={"connectionId": str(uuid.uuid4()), "event": "logged_out"},
            headers={"X-Gateway-Key": "wrong"},
        )
        assert r.status_code == 403

    async def test_unknown_connection_ignored(
        self, client, registered_user, monkeypatch
    ):
        r = await client.post(
            "/api/v1/internal/whatsapp-qr/events",
            json={"connectionId": str(uuid.uuid4()), "event": "logged_out"},
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"

    async def test_logged_out_marks_row_and_raises_alert(
        self, client, registered_user, monkeypatch, db_session
    ):
        from app.models.system_alert import SystemAlert

        org_id = registered_user["user"]["org_id"]
        row = await self._row_with_status(
            db_session, org_id, WhatsAppConnectionStatus.CONNECTED
        )
        r = await client.post(
            "/api/v1/internal/whatsapp-qr/events",
            json={"connectionId": str(row.id), "event": "logged_out"},
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 200 and r.json()["status"] == "ok"
        await db_session.refresh(row)
        assert row.status == "LOGGED_OUT"
        alerts = (
            await db_session.execute(
                select(SystemAlert).where(
                    SystemAlert.org_id == org_id,
                    SystemAlert.code == "WHATSAPP_LOGGED_OUT",
                    SystemAlert.resolved_at.is_(None),
                )
            )
        ).scalars().all()
        assert len(alerts) == 1

        # Repeat push dedupes (no second open alert).
        r2 = await client.post(
            "/api/v1/internal/whatsapp-qr/events",
            json={"connectionId": str(row.id), "event": "logged_out"},
            headers=self._headers(monkeypatch),
        )
        assert r2.json()["status"] == "ok"

    async def test_connected_resolves_alert(
        self, client, registered_user, monkeypatch, db_session
    ):
        from app.models.system_alert import SystemAlert

        org_id = registered_user["user"]["org_id"]
        row = await self._row_with_status(
            db_session, org_id, WhatsAppConnectionStatus.CONNECTED
        )
        h = self._headers(monkeypatch)
        await client.post(
            "/api/v1/internal/whatsapp-qr/events",
            json={"connectionId": str(row.id), "event": "logged_out"},
            headers=h,
        )
        r = await client.post(
            "/api/v1/internal/whatsapp-qr/events",
            json={"connectionId": str(row.id), "event": "connected"},
            headers=h,
        )
        assert r.status_code == 200 and r.json()["status"] == "ok"
        await db_session.refresh(row)
        assert row.status == "CONNECTED"
        open_alerts = (
            await db_session.execute(
                select(SystemAlert).where(
                    SystemAlert.org_id == org_id,
                    SystemAlert.code == "WHATSAPP_LOGGED_OUT",
                    SystemAlert.resolved_at.is_(None),
                )
            )
        ).scalars().all()
        assert open_alerts == []

    async def test_unknown_event_ignored(
        self, client, registered_user, monkeypatch, db_session
    ):
        org_id = registered_user["user"]["org_id"]
        row = await self._row_with_status(
            db_session, org_id, WhatsAppConnectionStatus.CONNECTED
        )
        r = await client.post(
            "/api/v1/internal/whatsapp-qr/events",
            json={"connectionId": str(row.id), "event": "something-else"},
            headers=self._headers(monkeypatch),
        )
        assert r.json()["status"] == "ignored"
        await db_session.refresh(row)
        assert row.status == "CONNECTED"
    async def test_message_sent_marks_latest_outbound(
        self, client, registered_user, monkeypatch, db_session
    ):
        org_id = registered_user["user"]["org_id"]
        row = await self._row_with_status(
            db_session, org_id, WhatsAppConnectionStatus.CONNECTED
        )
        session = ChatSession(
            org_id=org_id,
            channel="whatsapp",
            channel_user_id="919521433306",
            state_data={},
            missing_fields=[],
            session_status="ACTIVE",
        )
        db_session.add(session)
        await db_session.flush()
        message = ChatMessage(
            session_id=session.id,
            role="agent",
            content="Reply",
            sender_type="ai",
            direction="outbound",
            send_status="GENERATED",
        )
        db_session.add(message)
        await db_session.commit()

        response = await client.post(
            "/api/v1/internal/whatsapp-qr/events",
            json={
                "connectionId": str(row.id),
                "event": "message_sent",
                "sessionId": str(session.id),
                "fromNumber": "919521433306",
                "providerMessageId": "BAILEYS-SENT-1",
            },
            headers=self._headers(monkeypatch),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        await db_session.refresh(message)
        assert message.send_status == "SENT"
        assert message.provider_message_id == "BAILEYS-SENT-1"


# ── Booking toggle gate ──────────────────────────────────────────────────

class TestBookingToggle:
    def _headers(self, monkeypatch):
        monkeypatch.setattr(settings, "WA_GATEWAY_SERVICE_KEY", "test-gw-key")
        return {"X-Gateway-Key": "test-gw-key"}

    def _payload(self, connection_id, **over):
        body = {
            "connectionId": str(connection_id),
            "messageId": "wamid.HBgMtoggle1",
            "from": "919521433306",
            "chatJid": "919521433306@s.whatsapp.net",
            "text": "Hi, I need an appointment",
        }
        body.update(over)
        return body

    async def _cfg(self, db_session, org_id):
        from app.models.config import OrgConfig

        cfg = (
            await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
        ).scalars().first()
        assert cfg is not None  # registration creates it
        return cfg

    async def test_toggle_requires_existing_row(
        self, client, registered_user
    ):
        h = registered_user["headers"]
        r = await client.post(
            "/api/v1/dashboard/whatsapp-qr/booking-toggle",
            json={"enabled": True},
            headers=h,
        )
        assert r.status_code == 404

    async def test_toggle_roundtrip_updates_snapshot(
        self, client, registered_user, monkeypatch, db_session
    ):
        h = registered_user["headers"]
        org_id = registered_user["user"]["org_id"]
        db_session.add(
            WhatsAppConnection(org_id=org_id, status="CONNECTED")
        )
        await db_session.commit()
        fake = FakeGateway()
        _patch(monkeypatch, fake)

        r = await client.get(
            "/api/v1/dashboard/whatsapp-qr/status", headers=h
        )
        assert r.status_code == 200
        assert r.json()["booking_enabled"] is False
        assert r.json()["booking_active"] is False

        # Enabling WhatsApp booking must NOT touch OTP flags: an explicit
        # admin OFF always wins (auto-enable silently reverted the Agent
        # page setting on every toggle). Force both off first so the
        # toggle-on must leave them off.
        from app.models.config import OrgConfig

        cfg = (
            await db_session.execute(
                select(OrgConfig).where(
                    OrgConfig.org_id == registered_user["user"]["org_id"]
                )
            )
        ).scalars().first()
        cfg.require_otp = False
        cfg.require_phone_otp = False
        await db_session.commit()

        r = await client.post(
            "/api/v1/dashboard/whatsapp-qr/booking-toggle",
            json={"enabled": True},
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert r.json()["booking_enabled"] is True
        await db_session.refresh(cfg)
        assert cfg.require_otp is False
        assert cfg.require_phone_otp is False  # phone OTP stays independently controllable

        r = await client.post(
            "/api/v1/dashboard/whatsapp-qr/booking-toggle",
            json={"enabled": False},
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["booking_enabled"] is False

    async def test_inbound_ignored_when_toggle_off(
        self, client, registered_user, monkeypatch, db_session
    ):
        from types import SimpleNamespace
        from app.engine import runtime_router

        calls = []

        async def fake_route(*, db, org, org_config, chat_session, message_text,
                             background_tasks=None, transcript_user_text=None,
                             inbound_message_id=None):
            calls.append(message_text)
            return SimpleNamespace(widget_directive="TEXT_INPUT", message="Hi!", field_meta=None)

        monkeypatch.setattr(runtime_router, "route_whatsapp_message", fake_route)
        org_id = registered_user["user"]["org_id"]
        row = WhatsAppConnection(org_id=org_id, status="CONNECTED")
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)

        r = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload(row.id),
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"
        assert r.json()["reason"] == "disabled_by_admin"
        assert calls == []  # engine never invoked

    async def test_inbound_proceeds_regardless_of_meeting_mode(
        self, client, registered_user, monkeypatch, db_session
    ):
        """Meeting mode describes the meeting type, not channel permission:
        toggle ON + OFFLINE_ONLY still processes inbound."""
        from types import SimpleNamespace
        from app.engine import runtime_router
        from app.models.config import MeetingMode

        calls = []

        async def fake_route(*, db, org, org_config, chat_session, message_text,
                             background_tasks=None, transcript_user_text=None,
                             inbound_message_id=None):
            calls.append(message_text)
            return SimpleNamespace(widget_directive="TEXT_INPUT", message="Hi!", field_meta=None)

        monkeypatch.setattr(runtime_router, "route_whatsapp_message", fake_route)
        org_id = registered_user["user"]["org_id"]
        cfg = await self._cfg(db_session, org_id)
        cfg.meeting_mode = MeetingMode.OFFLINE_ONLY  # explicit offline
        row = WhatsAppConnection(
            org_id=org_id, status="CONNECTED", whatsapp_booking_enabled=True
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)

        r = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload(row.id, messageId="wamid.HBgMtoggle2"),
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok"
        assert calls == ["Hi, I need an appointment"]

    async def test_inbound_proceeds_when_enabled_and_hybrid(
        self, client, registered_user, monkeypatch, db_session
    ):
        from types import SimpleNamespace
        from app.engine import runtime_router
        from app.models.config import MeetingMode

        calls = []

        async def fake_route(*, db, org, org_config, chat_session, message_text,
                             background_tasks=None, transcript_user_text=None,
                             inbound_message_id=None):
            calls.append(message_text)
            return SimpleNamespace(widget_directive="TEXT_INPUT", message="Hi!", field_meta=None)

        monkeypatch.setattr(runtime_router, "route_whatsapp_message", fake_route)
        org_id = registered_user["user"]["org_id"]
        cfg = await self._cfg(db_session, org_id)
        cfg.meeting_mode = MeetingMode.HYBRID
        row = WhatsAppConnection(
            org_id=org_id, status="CONNECTED", whatsapp_booking_enabled=True
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)

        r = await client.post(
            "/api/v1/internal/whatsapp-qr/inbound",
            json=self._payload(row.id, messageId="wamid.HBgMtoggle3"),
            headers=self._headers(monkeypatch),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok"
        assert calls == ["Hi, I need an appointment"]
