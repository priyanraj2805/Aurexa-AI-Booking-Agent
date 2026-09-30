"""Meta WhatsApp Cloud API webhook — receive-only, no engine calls.

Covers: GET handshake (ok / wrong token / wrong mode), POST text,
button_reply, list_reply, status-only, malformed, wrong phone_number_id,
multi-entry payloads, and duplicate message_id via the shared debouncer.

Every POST here is signed with the App Secret. Inbound POSTs are
authenticated (X-Hub-Signature-256, verified against the raw body), so an
unsigned request is rejected before normalisation. The auth layer itself is
covered in test_whatsapp_webhook_signature.py; these tests deliberately sign so
they keep exercising the pipeline rather than the guard.
"""
import hashlib
import hmac
import json
import uuid

import pytest

from app.core.config import settings

PATH = "/api/v1/whatsapp/webhook"
PNID = "1288066011058334"
APP_SECRET = "test-app-secret-123"


def _signed(payload):
    """(raw_body, headers) for a correctly signed webhook POST."""
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    digest = hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={digest}"}


async def _post(client, payload):
    raw, headers = _signed(payload)
    return await client.post(PATH, content=raw, headers=headers)


def _msg(msg_id, msg, pnid=PNID):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "4439243576313653",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "919521433306",
                                "phone_number_id": pnid,
                            },
                            "contacts": [{"profile": {"name": "Test User"}, "wa_id": "919999999978"}],
                            "messages": [msg] if msg is not None else [],
                        },
                    }
                ],
            }
        ],
    }


def _text_msg(mid="wamid.text1"):
    return {"from": "919999999978", "id": mid, "timestamp": "1757930000", "type": "text",
            "text": {"body": "I want to book"}}


def _button_msg(mid="wamid.btn1"):
    return {"from": "919999999978", "id": mid, "timestamp": "1757930001", "type": "interactive",
            "interactive": {"type": "button_reply",
                            "button_reply": {"id": "confirm_booking", "title": "Confirm"}}}


def _list_msg(mid="wamid.list1"):
    return {"from": "919999999978", "id": mid, "timestamp": "1757930002", "type": "interactive",
            "interactive": {"type": "list_reply",
                            "list_reply": {"id": "date_20260927", "title": "27 September",
                                           "description": "Saturday"}}}


@pytest.fixture
def _token(monkeypatch):
    monkeypatch.setattr(settings, "META_WHATSAPP_WEBHOOK_VERIFY_TOKEN", "test-verify-token-123")
    monkeypatch.setattr(settings, "META_WHATSAPP_PHONE_NUMBER_ID", PNID)
    monkeypatch.setattr(settings, "META_WHATSAPP_APP_SECRET", APP_SECRET, raising=False)
    return "test-verify-token-123"


@pytest.mark.asyncio
async def test_get_verify_success(client, _token):
    r = await client.get(PATH, params={"hub.mode": "subscribe",
                                       "hub.verify_token": _token,
                                       "hub.challenge": "CHALLENGE_ABC"})
    assert r.status_code == 200
    assert r.text == "CHALLENGE_ABC"


@pytest.mark.asyncio
async def test_get_verify_wrong_token(client, _token):
    r = await client.get(PATH, params={"hub.mode": "subscribe",
                                       "hub.verify_token": "wrong",
                                       "hub.challenge": "CHALLENGE_ABC"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_verify_wrong_mode(client, _token):
    r = await client.get(PATH, params={"hub.mode": "unsubscribe",
                                       "hub.verify_token": _token,
                                       "hub.challenge": "CHALLENGE_ABC"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_post_text(client, _token):
    mid = f"wamid.text.{uuid.uuid4().hex[:8]}"
    r = await _post(client, _msg(mid, _text_msg(mid)))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["provider"] == "meta_cloud"
    ev = body["events"][0]
    assert ev["message_type"] == "text" and ev["text"] == "I want to book"
    assert ev["option_id"] is None and ev["message_id"] == mid
    assert ev["from"] == "919999999978" and ev["phone_number_id"] == PNID


@pytest.mark.asyncio
async def test_post_button_reply(client, _token):
    mid = f"wamid.btn.{uuid.uuid4().hex[:8]}"
    r = await _post(client, _msg(mid, _button_msg(mid)))
    assert r.status_code == 200
    ev = r.json()["events"][0]
    assert ev["message_type"] == "interactive" and ev["option_id"] == "confirm_booking"
    assert ev["text"] == "Confirm" and ev["raw_type"] == "button_reply"


@pytest.mark.asyncio
async def test_post_list_reply(client, _token):
    mid = f"wamid.list.{uuid.uuid4().hex[:8]}"
    r = await _post(client, _msg(mid, _list_msg(mid)))
    assert r.status_code == 200
    ev = r.json()["events"][0]
    assert ev["message_type"] == "interactive" and ev["option_id"] == "date_20260927"
    assert ev["text"] == "27 September" and ev["raw_type"] == "list_reply"


@pytest.mark.asyncio
async def test_post_status_only(client, _token):
    payload = _msg("x", None)
    payload["entry"][0]["changes"][0]["value"]["statuses"] = [
        {"id": "wamid.s1", "status": "delivered", "timestamp": "1757930003",
         "recipient_id": "919999999978"}]
    r = await _post(client, payload)
    assert r.status_code == 200
    body = r.json()
    assert body["events"] == [] and body["statuses"] == 1


@pytest.mark.asyncio
async def test_post_malformed(client, _token):
    r = await _post(client, b"not-json{{{")
    assert r.status_code in (200, 400, 422)
    r2 = await _post(client, {"object": "something_else"})
    assert r2.status_code == 200 and r2.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_post_wrong_pnid(client, _token):
    mid = f"wamid.wp.{uuid.uuid4().hex[:8]}"
    r = await _post(client, _msg(mid, _text_msg(mid), pnid="9999999999999999"))
    assert r.status_code == 200
    assert r.json()["events"] == []


@pytest.mark.asyncio
async def test_post_multi_entry(client, _token):
    m1, m2 = f"wamid.m1.{uuid.uuid4().hex[:8]}", f"wamid.m2.{uuid.uuid4().hex[:8]}"
    payload = _msg(m1, _text_msg(m1))
    second = {"id": "4439243576313653",
              "changes": [{"field": "messages",
                           "value": {"messaging_product": "whatsapp",
                                     "metadata": {"display_phone_number": "919521433306",
                                                  "phone_number_id": PNID},
                                     "contacts": [],
                                     "messages": [_button_msg(m2)]}}]}
    payload["entry"].append(second)
    r = await _post(client, payload)
    assert r.status_code == 200
    body = r.json()
    assert len(body["events"]) == 2
    assert {e["message_id"] for e in body["events"]} == {m1, m2}


@pytest.mark.asyncio
async def test_post_duplicate(client, _token):
    mid = f"wamid.dup.{uuid.uuid4().hex[:8]}"
    first = await _post(client, _msg(mid, _text_msg(mid)))
    assert first.status_code == 200 and first.json()["duplicates"] == 0
    second = await _post(client, _msg(mid, _text_msg(mid)))
    assert second.status_code == 200
    body = second.json()
    assert body["duplicates"] == 1 and body["events"][0].get("duplicate") is True


# ── Pipeline wiring (Cloud inbound -> engine -> Cloud reply) ─────────────

_phone_seq = [919990000000]


def _test_phone():
    _phone_seq[0] += 1
    return str(_phone_seq[0])


async def _seed_org(db_session, enabled=True):
    import uuid as _uuid
    from sqlalchemy import update as _update
    from app.models.organization import Organization
    from app.models.config import OrgConfig
    from app.models.whatsapp_connection import WhatsAppConnection

    # Shared in-memory DB across tests: neutralize other orgs' toggles first
    # so each wiring test controls exactly what _resolve_booking_org sees.
    await db_session.execute(_update(WhatsAppConnection).values(whatsapp_booking_enabled=False))
    org = Organization(name="Cloud Webhook Org", api_key=f"ds_cw_{_uuid.uuid4().hex[:12]}",
                       is_active=True)
    db_session.add(org)
    await db_session.flush()
    db_session.add(OrgConfig(org_id=org.id, required_fields=["name", "email", "phone", "date", "time"]))
    db_session.add(WhatsAppConnection(org_id=org.id, status="CONNECTED",
                                      whatsapp_booking_enabled=enabled))
    await db_session.commit()
    return org


def _cloud_text(phone, mid, body="Hi"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "4439243576313653",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "919521433306",
                                  "phone_number_id": PNID},
                    "contacts": [],
                    "messages": [{"from": phone, "id": mid, "timestamp": "1757930000",
                                  "type": "text", "text": {"body": body}}],
                }}]}]}


@pytest.mark.asyncio
async def test_cloud_hi_runs_engine_and_sends(client, _token, db_session, monkeypatch):
    import app.engine.runtime_router as _rr
    import app.core.whatsapp_cloud as _wc
    import app.core.whatsapp_templates as _wt
    from app.schemas.agent import ActionPayload
    from app.models.session import ChatSession
    from sqlalchemy.future import select as _select

    await _seed_org(db_session, enabled=True)
    calls = {}

    async def _fake_engine(**kwargs):
        calls["engine"] = kwargs.get("message_text")
        return ActionPayload(message="Could you please share your full name?",
                             widget_directive="TEXT_INPUT",
                             field_meta={"name": "name"})

    async def _fake_send(to, wa_msg):
        calls["send_to"] = to
        calls["send_msg"] = wa_msg
        return {"success": True, "wamid": "wamid.fake"}

    async def _fake_welcome(to, name, business):
        calls["welcome"] = (to, name, business)
        return {"success": True}

    monkeypatch.setattr(_rr, "route_whatsapp_message", _fake_engine)
    monkeypatch.setattr(_wc, "send_booking_reply", _fake_send)
    monkeypatch.setattr(_wt, "send_welcome", _fake_welcome)

    phone = _test_phone()
    mid = f"wamid.wire.{uuid.uuid4().hex[:8]}"
    r = await _post(client, _cloud_text(phone, mid))
    assert r.status_code == 200
    body = r.json()
    assert body["processed"] == 1
    assert calls.get("engine") == "Hi"
    assert calls.get("send_to") == phone
    assert calls.get("welcome", (None,))[0] == phone
    row = (await db_session.execute(
        _select(ChatSession).where(ChatSession.channel_user_id == phone))).scalars().first()
    assert row is not None and row.channel == "whatsapp_cloud"


@pytest.mark.asyncio
async def test_cloud_toggle_off_skips(client, _token, db_session, monkeypatch):
    import app.engine.runtime_router as _rr
    import app.core.whatsapp_cloud as _wc

    await _seed_org(db_session, enabled=False)
    calls = []

    async def _fake_engine(**kwargs):
        calls.append("engine")

    async def _fake_send(to, wa_msg):
        calls.append("send")
        return {"success": True}

    monkeypatch.setattr(_rr, "route_whatsapp_message", _fake_engine)
    monkeypatch.setattr(_wc, "send_booking_reply", _fake_send)

    phone = _test_phone()
    mid = f"wamid.off.{uuid.uuid4().hex[:8]}"
    r = await _post(client, _cloud_text(phone, mid))
    assert r.status_code == 200 and r.json()["processed"] == 0
    assert calls == []


@pytest.mark.asyncio
async def test_cloud_duplicate_skips_engine(client, _token, db_session, monkeypatch):
    import app.engine.runtime_router as _rr

    await _seed_org(db_session, enabled=True)
    calls = []

    async def _fake_engine(**kwargs):
        calls.append("engine")
        from app.schemas.agent import ActionPayload
        return ActionPayload(message="ok", widget_directive="TEXT_INPUT")

    monkeypatch.setattr(_rr, "route_whatsapp_message", _fake_engine)

    phone = _test_phone()
    mid = f"wamid.wdup.{uuid.uuid4().hex[:8]}"
    first = await _post(client, _cloud_text(phone, mid))
    assert first.status_code == 200
    n_calls = len(calls)
    second = await _post(client, _cloud_text(phone, mid))
    assert second.status_code == 200 and second.json()["processed"] == 0
    assert len(calls) == n_calls


def test_step_template_guard_roundtrip():
    from app.core import whatsapp_templates as _wt

    assert _wt.step_template_already_sent({}, _wt.TEMPLATE_WELCOME, ["name"]) is False
    sd = _wt.mark_step_template_sent({}, _wt.TEMPLATE_WELCOME, ["name", "email"])
    assert _wt.step_template_already_sent(sd, _wt.TEMPLATE_WELCOME, ["email", "name"]) is True
    # Step advanced (name captured) -> guard releases, other templates unaffected
    assert _wt.step_template_already_sent(sd, _wt.TEMPLATE_WELCOME, ["email"]) is False
    assert _wt.step_template_already_sent(sd, _wt.TEMPLATE_REQUEST_EMAIL, ["name", "email"]) is False


@pytest.mark.asyncio
async def test_cloud_template_sent_once_per_step(client, _token, db_session, monkeypatch):
    import app.engine.runtime_router as _rr
    import app.core.whatsapp_cloud as _wc
    import app.core.whatsapp_templates as _wt
    from app.schemas.agent import ActionPayload

    await _seed_org(db_session, enabled=True)
    calls = {"welcome": 0, "send": 0}

    async def _fake_engine(**kwargs):
        return ActionPayload(message="Could you please share your full name?",
                             widget_directive="TEXT_INPUT",
                             field_meta={"name": "name"})

    async def _fake_send(to, wa_msg):
        calls["send"] += 1
        return {"success": True, "wamid": "wamid.fake"}

    async def _fake_welcome(to, name, business):
        calls["welcome"] += 1
        return {"success": True}

    monkeypatch.setattr(_rr, "route_whatsapp_message", _fake_engine)
    monkeypatch.setattr(_wc, "send_booking_reply", _fake_send)
    monkeypatch.setattr(_wt, "send_welcome", _fake_welcome)

    phone = _test_phone()
    first = await _post(client, _cloud_text(phone, f"wamid.g1.{uuid.uuid4().hex[:8]}"))
    assert first.status_code == 200 and first.json()["processed"] == 1
    second = await _post(client, _cloud_text(phone, f"wamid.g2.{uuid.uuid4().hex[:8]}", body="Hi again"))
    assert second.status_code == 200 and second.json()["processed"] == 1
    assert calls["welcome"] == 1, "stuck step must not re-fire the billable template"
    assert calls["send"] == 2, "text reply still goes out every turn"


@pytest.mark.asyncio
async def test_cloud_unknown_type_skips_engine(client, _token, db_session, monkeypatch):
    import app.engine.runtime_router as _rr

    await _seed_org(db_session, enabled=True)
    calls = []

    async def _fake_engine(**kwargs):
        calls.append("engine")

    monkeypatch.setattr(_rr, "route_whatsapp_message", _fake_engine)

    phone = _test_phone()
    mid = f"wamid.unk.{uuid.uuid4().hex[:8]}"
    payload = _cloud_text(phone, mid)
    payload["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "from": phone, "id": mid, "timestamp": "1757930000", "type": "sticker",
        "sticker": {"id": "123"}}
    r = await _post(client, payload)
    assert r.status_code == 200
    body = r.json()
    assert body["events"][0]["message_type"] == "unknown" and body["processed"] == 0
    assert calls == []
