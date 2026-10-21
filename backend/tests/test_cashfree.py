"""Cashfree provider: signature verify, order create, webhook mark-paid."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.session import ChatSession
from app.models.payment import PaymentAttempt
from app.services.cashfree.client import CashfreeClient, set_http_client_factory
from app.services.cashfree.webhooks import verify_webhook_signature


SECRET = "test_secret_key_123"


def _sign(body: bytes, ts: str) -> str:
    digest = hmac.new(SECRET.encode(), f"{ts}".encode() + body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_verify_valid_signature():
    body = b'{"type":"PAYMENT_SUCCESS_WEBHOOK","data":{}}'
    ts = str(int(time.time()))
    assert verify_webhook_signature(_sign(body, ts), body, ts, SECRET) is True


def test_verify_rejects_bad_signature():
    body = b'{"type":"PAYMENT_SUCCESS_WEBHOOK"}'
    ts = str(int(time.time()))
    assert verify_webhook_signature("bogus", body, ts, SECRET) is False


def test_verify_rejects_stale_timestamp():
    body = b'{}'
    ts = str(int(time.time()) - 3600)
    assert verify_webhook_signature(_sign(body, ts), body, ts, SECRET) is False


def test_verify_rejects_missing_parts():
    assert verify_webhook_signature("", b"{}", str(int(time.time())), SECRET) is False
    assert verify_webhook_signature("x", b"{}", "", SECRET) is False
    assert verify_webhook_signature("x", b"{}", str(int(time.time())), "") is False


def _mock_client(handler):
    def _factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return _factory


async def test_create_order_posts_pg_orders():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={"order_id": "cf_test_1", "payment_session_id": "sess_abc", "order_status": "ACTIVE"},
        )

    set_http_client_factory(_mock_client(handler))
    try:
        client = CashfreeClient(
            app_id="TESTID", secret_key="SECRET",
            api_base="https://sandbox.cashfree.com/pg", api_version="2026-01-01",
        )
        # enabled flag lives in settings; client itself only needs creds
        order = await client.create_order(
            order_id="cf_test_1", order_amount=500.00, customer_email="a@b.c",
        )
    finally:
        set_http_client_factory(None)
    assert order["payment_session_id"] == "sess_abc"
    assert seen["url"] == "https://sandbox.cashfree.com/pg/orders"
    assert seen["headers"]["x-client-id"] == "TESTID"
    assert "x-client-secret" in seen["headers"]
    assert seen["headers"]["x-api-version"] == "2026-01-01"


async def _org_with_price(db_session: AsyncSession) -> Organization:
    org = Organization(
        name="Cashfree Org",
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
        )
    )
    await db_session.commit()
    await db_session.refresh(org)
    return org


async def _session(db_session: AsyncSession, org_id) -> ChatSession:
    sess = ChatSession(
        org_id=org_id,
        state_data={"name": "Pay User", "email": "pay@test.com", "phone": "+911234567890"},
        missing_fields=[],
        current_intent="CONFIRM_BOOKING",
        session_status="ACTIVE",
        otp_verified=True,
    )
    db_session.add(sess)
    await db_session.commit()
    await db_session.refresh(sess)
    return sess


async def test_order_endpoint_disabled_by_default(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_ENABLED", False)
    org = await _org_with_price(db_session)
    sess = await _session(db_session, org.id)
    resp = await client.post(
        "/api/v1/widget/payment/cashfree/order",
        json={"session_id": str(sess.id), "api_key": org.api_key},
    )
    # Fail closed with a clean status when disabled.
    assert resp.status_code == 503


async def test_order_creates_cashfree_attempt(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_ENABLED", True)
    monkeypatch.setattr(settings, "CASHFREE_APP_ID", "TESTID")
    monkeypatch.setattr(settings, "CASHFREE_SECRET_KEY", "SECRET")
    monkeypatch.setattr(settings, "CASHFREE_API_BASE", "https://sandbox.cashfree.com/pg")
    monkeypatch.setattr(settings, "PUBLIC_API_BASE", "")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"order_id": "cf_test_9", "payment_session_id": "psess_9", "order_status": "ACTIVE"},
        )

    set_http_client_factory(_mock_client(handler))
    try:
        org = await _org_with_price(db_session)
        sess = await _session(db_session, org.id)
        resp = await client.post(
            "/api/v1/widget/payment/cashfree/order",
            json={"session_id": str(sess.id), "api_key": org.api_key},
        )
    finally:
        set_http_client_factory(None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_session_id"] == "psess_9"
    assert body["amount_paise"] == 50000
    rows = (
        (await db_session.execute(select(PaymentAttempt).where(PaymentAttempt.provider_order_id == "cf_test_9")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == "created"
    assert rows[0].provider == "cashfree"


async def test_webhook_marks_paid_and_dedupes(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_SECRET_KEY", SECRET)

    org = await _org_with_price(db_session)
    sess = await _session(db_session, org.id)
    attempt = PaymentAttempt(
        org_id=org.id,
        session_id=sess.id,
        provider="cashfree",
        provider_order_id="cf_hook_1",
        amount_paise=50000,
        currency="INR",
        status="created",
        verification_status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db_session.add(attempt)
    await db_session.commit()

    payload = {
        "type": "PAYMENT_SUCCESS_WEBHOOK",
        "data": {
            "order": {"order_id": "cf_hook_1", "order_amount": 500.00, "order_currency": "INR"},
            "payment": {"cf_payment_id": 885299238, "payment_status": "SUCCESS", "payment_amount": 500.00},
            "customer_details": {},
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
    assert attempt.provider_payment_id == "885299238"

    # Retry delivery → deduplicated, still paid exactly once.
    r2 = await client.post("/api/v1/widget/payment/cashfree/webhook", content=raw, headers=headers)
    assert r2.json().get("deduplicated") is True


async def test_webhook_rejects_bad_signature(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_SECRET_KEY", SECRET)
    raw = b'{"type":"PAYMENT_SUCCESS_WEBHOOK","data":{}}'
    r = await client.post(
        "/api/v1/widget/payment/cashfree/webhook",
        content=raw,
        headers={"x-webhook-signature": "nope", "x-webhook-timestamp": str(int(time.time()))},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


async def test_webhook_rejects_amount_mismatch(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_SECRET_KEY", SECRET)
    org = await _org_with_price(db_session)
    sess = await _session(db_session, org.id)
    attempt = PaymentAttempt(
        org_id=org.id,
        session_id=sess.id,
        provider="cashfree",
        provider_order_id="cf_hook_2",
        amount_paise=50000,
        currency="INR",
        status="created",
        verification_status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db_session.add(attempt)
    await db_session.commit()

    payload = {
        "type": "PAYMENT_SUCCESS_WEBHOOK",
        "data": {
            "order": {"order_id": "cf_hook_2", "order_amount": 500.00},
            "payment": {"cf_payment_id": 1, "payment_status": "SUCCESS", "payment_amount": 10.00},
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
