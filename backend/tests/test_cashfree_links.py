"""Cashfree payment links for WhatsApp: client, webhook, reuse."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.session import ChatSession
from app.models.payment import PaymentAttempt
from app.schemas.agent import ActionPayload
from app.services.cashfree.client import CashfreeClient, set_http_client_factory
from app.services.payment_service import payment_service


SECRET = "test_secret_key_123"


def _sign(body: bytes, ts: str) -> str:
    digest = hmac.new(SECRET.encode(), f"{ts}".encode() + body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _mock_client(handler):
    def _factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return _factory


async def _org_cashfree(db_session: AsyncSession) -> Organization:
    org = Organization(
        name="Cashfree WA Org",
        api_key=f"ds_test_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )
    db_session.add(org)
    await db_session.flush()
    db_session.add(
        OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
            appointment_price_enabled=True,
            appointment_price_paise=50000,
            payment_provider="cashfree",
        )
    )
    await db_session.commit()
    await db_session.refresh(org)
    return org


async def _wa_session(db_session: AsyncSession, org_id) -> ChatSession:
    sess = ChatSession(
        org_id=org_id,
        channel="whatsapp",
        state_data={"name": "WA User", "email": "wa@test.com", "phone": "+919876543210"},
        missing_fields=[],
        current_intent="CONFIRM_BOOKING",
        session_status="ACTIVE",
    )
    db_session.add(sess)
    await db_session.commit()
    await db_session.refresh(sess)
    return sess


def _platform_keys(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_ENABLED", True)
    monkeypatch.setattr(settings, "CASHFREE_APP_ID", "TESTID")
    monkeypatch.setattr(settings, "CASHFREE_SECRET_KEY", SECRET)
    monkeypatch.setattr(settings, "CASHFREE_API_BASE", "https://sandbox.cashfree.com/pg")
    monkeypatch.setattr(settings, "PUBLIC_API_BASE", "")


async def test_create_link_posts_pg_links():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"link_id": "cfl_abc", "link_url": "https://payments-test.cashfree.com/links/abc", "link_status": "ACTIVE"},
        )

    set_http_client_factory(_mock_client(handler))
    try:
        client = CashfreeClient(
            app_id="TESTID", secret_key="SECRET",
            api_base="https://sandbox.cashfree.com/pg", api_version="2026-01-01",
        )
        resp = await client.create_payment_link(
            link_id="cfl_abc", link_amount=500.00, link_currency="INR",
            link_purpose="Appointment booking payment",
            customer_phone="919876543210", customer_email="wa@test.com",
            notify_url="https://x.test/webhook",
        )
    finally:
        set_http_client_factory(None)
    assert resp["link_url"].endswith("/links/abc")
    assert seen["url"] == "https://sandbox.cashfree.com/pg/links"
    body = seen["body"]
    assert body["link_id"] == "cfl_abc"
    assert body["link_amount"] == 500.00
    assert body["customer_details"]["customer_phone"] == "919876543210"
    assert body["link_notify"] == {"send_sms": False, "send_email": False, "send_whatsapp": False}
    assert body["link_meta"]["upi_intent"] is True
    assert body["link_meta"]["notify_url"] == "https://x.test/webhook"


async def test_create_link_raises_without_url():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"link_id": "cfl_x", "link_status": "ACTIVE"})

    set_http_client_factory(_mock_client(handler))
    try:
        client = CashfreeClient(app_id="T", secret_key="S")
        with pytest.raises(Exception):
            await client.create_payment_link(link_id="cfl_x", link_amount=1.0, customer_phone="919999999999")
    finally:
        set_http_client_factory(None)


async def test_ensure_link_creates_attempt_and_reuses(
    db_session: AsyncSession, monkeypatch
):
    _platform_keys(monkeypatch)
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200, json={"link_id": "cfl_reuse", "link_url": "https://pay.test/cfl_reuse", "link_status": "ACTIVE"}
        )

    set_http_client_factory(_mock_client(handler))
    try:
        org = await _org_cashfree(db_session)
        sess = await _wa_session(db_session, org.id)
        cfg = (await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))).scalars().first()

        url1 = await payment_service.ensure_whatsapp_payment_link(
            db_session, org, cfg, sess, 50000, "INR"
        )
        assert url1 == "https://pay.test/cfl_reuse"
        assert len([u for u in calls if u.endswith("/pg/links")]) == 1

        # Second call reuses the live link instead of minting a new one.
        url2 = await payment_service.ensure_whatsapp_payment_link(
            db_session, org, cfg, sess, 50000, "INR"
        )
        assert url2 == url1
        assert len([u for u in calls if u.endswith("/pg/links")]) == 1

        rows = (
            await db_session.execute(
                select(PaymentAttempt).where(PaymentAttempt.provider_order_id.like("cfl_%"))
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].checkout_url == url1
        assert rows[0].status == "created"
    finally:
        set_http_client_factory(None)


async def test_attach_link_only_on_whatsapp_cashfree(
    db_session: AsyncSession, monkeypatch
):
    _platform_keys(monkeypatch)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"link_id": "cfl_att", "link_url": "https://pay.test/cfl_att", "link_status": "ACTIVE"}
        )

    set_http_client_factory(_mock_client(handler))
    try:
        org = await _org_cashfree(db_session)
        sess = await _wa_session(db_session, org.id)
        cfg = (await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))).scalars().first()

        # WhatsApp channel gets the link appended.
        sess.channel = "whatsapp"
        action = ActionPayload(
            message="Your appointment total is ₹500.00. Please pay to confirm your booking.",
            widget_directive="REQUIRE_PAYMENT",
            field_meta={"amount_paise": 50000, "currency": "INR", "payment_provider": "cashfree"},
        )
        out = await payment_service.attach_whatsapp_payment_link(db_session, org, cfg, sess, action)
        assert "https://pay.test/cfl_att" in out.message

        # Web channel is untouched (widget uses the JS checkout).
        sess2 = await _wa_session(db_session, org.id)
        sess2.channel = "web"
        action2 = ActionPayload(
            message="Your appointment total is ₹500.00. Please pay to confirm your booking.",
            widget_directive="REQUIRE_PAYMENT",
            field_meta={"amount_paise": 50000, "currency": "INR", "payment_provider": "cashfree"},
        )
        out2 = await payment_service.attach_whatsapp_payment_link(db_session, org, cfg, sess2, action2)
        assert "https://pay.test" not in out2.message
    finally:
        set_http_client_factory(None)


async def test_link_webhook_marks_paid_and_dedupes(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_SECRET_KEY", SECRET)
    org = await _org_cashfree(db_session)
    sess = await _wa_session(db_session, org.id)
    attempt = PaymentAttempt(
        org_id=org.id,
        session_id=sess.id,
        provider="cashfree",
        provider_order_id="cfl_hook_1",
        checkout_url="https://pay.test/cfl_hook_1",
        amount_paise=50000,
        currency="INR",
        status="created",
        verification_status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db_session.add(attempt)
    await db_session.commit()

    payload = {
        "type": "PAYMENT_LINK_EVENT",
        "data": {
            "link_id": "cfl_hook_1",
            "link_status": "PAID",
            "link_currency": "INR",
            "link_amount_paid": 500.00,
            "order": {"order_id": "CFPay_1", "transaction_status": "SUCCESS", "transaction_id": 4242},
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    headers = {"x-webhook-signature": _sign(raw, ts), "x-webhook-timestamp": ts}

    r1 = await client.post("/api/v1/widget/payment/cashfree/webhook", content=raw, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["ok"] is True

    await db_session.refresh(attempt)
    assert attempt.status == "paid"
    assert attempt.verification_status == "verified"

    r2 = await client.post("/api/v1/widget/payment/cashfree/webhook", content=raw, headers=headers)
    assert r2.json().get("deduplicated") is True


async def test_link_webhook_accepts_form_data(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_SECRET_KEY", SECRET)
    org = await _org_cashfree(db_session)
    sess = await _wa_session(db_session, org.id)
    attempt = PaymentAttempt(
        org_id=org.id,
        session_id=sess.id,
        provider="cashfree",
        provider_order_id="cfl_hook_form",
        checkout_url="https://pay.test/cfl_hook_form",
        amount_paise=50000,
        currency="INR",
        status="created",
        verification_status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db_session.add(attempt)
    await db_session.commit()

    raw = urlencode({
        "link_id": "cfl_hook_form",
        "link_status": "PAID",
        "link_amount_paid": "500.00",
        "link_currency": "INR",
    }).encode()
    ts = str(int(time.time()))
    headers = {
        "x-webhook-signature": _sign(raw, ts),
        "x-webhook-timestamp": ts,
        "content-type": "application/x-www-form-urlencoded",
    }
    r = await client.post("/api/v1/widget/payment/cashfree/webhook", content=raw, headers=headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    await db_session.refresh(attempt)
    assert attempt.status == "paid"


async def test_link_webhook_rejects_amount_mismatch(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_SECRET_KEY", SECRET)
    org = await _org_cashfree(db_session)
    sess = await _wa_session(db_session, org.id)
    attempt = PaymentAttempt(
        org_id=org.id,
        session_id=sess.id,
        provider="cashfree",
        provider_order_id="cfl_hook_3",
        checkout_url="https://pay.test/cfl_hook_3",
        amount_paise=50000,
        currency="INR",
        status="created",
        verification_status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db_session.add(attempt)
    await db_session.commit()

    payload = {
        "type": "PAYMENT_LINK_EVENT",
        "data": {
            "link_id": "cfl_hook_3",
            "link_status": "PAID",
            "link_currency": "INR",
            "link_amount_paid": 10.00,
            "order": {"transaction_status": "SUCCESS"},
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    r = await client.post(
        "/api/v1/widget/payment/cashfree/webhook",
        content=raw,
        headers={"x-webhook-signature": _sign(raw, ts), "x-webhook-timestamp": ts},
    )
    assert r.json()["ok"] is True
    await db_session.refresh(attempt)
    assert attempt.status == "created"
