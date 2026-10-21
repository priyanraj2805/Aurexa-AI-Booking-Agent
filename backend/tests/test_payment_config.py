"""Per-org Cashfree connect: encrypted creds, config API, org-key order path."""

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

FERNET_KEY = "ApUXU9ieORcDMIkhri0ascui4WsSKQg6jQjLWaqhR18="


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    monkeypatch.setenv("PAYMENT_CREDENTIALS_KEY", FERNET_KEY)


def test_crypto_roundtrip():
    from app.core import credential_crypto as crypto

    token = crypto.encrypt_secret("cfsk_secret_123")
    assert token != "cfsk_secret_123"
    assert crypto.decrypt_secret(token) == "cfsk_secret_123"
    assert crypto.mask_secret("TEST1129753249") == "…3249"


def test_crypto_fail_closed_without_key(monkeypatch):
    from app.core import credential_crypto as crypto

    monkeypatch.delenv("PAYMENT_CREDENTIALS_KEY", raising=False)
    with pytest.raises(ValueError):
        crypto.encrypt_secret("x")


async def _org(db_session: AsyncSession) -> Organization:
    org = Organization(
        name="CF Connect Org",
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
            appointment_price_paise=25000,
        )
    )
    await db_session.commit()
    await db_session.refresh(org)
    return org


async def _user_client(client: AsyncClient, db_session: AsyncSession, org: Organization):
    from app.models.user import User
    from app.core.security import hash_password, create_access_token

    user = User(
        email=f"owner-{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=hash_password("password123"),
        full_name="Owner",
        org_id=org.id,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    token = create_access_token(data={"sub": user.email, "org_id": str(org.id)})
    return {"Authorization": f"Bearer {token}"}


async def test_put_config_requires_keys_for_cashfree(
    client: AsyncClient, db_session: AsyncSession
):
    org = await _org(db_session)
    headers = await _user_client(client, db_session, org)
    r = await client.put(
        "/api/v1/dashboard/payments/config",
        json={"provider": "cashfree", "cashfree_mode": "test"},
        headers=headers,
    )
    assert r.status_code == 422


async def test_put_get_config_roundtrip_masks_secrets(
    client: AsyncClient, db_session: AsyncSession
):
    org = await _org(db_session)
    headers = await _user_client(client, db_session, org)
    r = await client.put(
        "/api/v1/dashboard/payments/config",
        json={
            "provider": "cashfree",
            "cashfree_app_id": "TEST1129753249",
            "cashfree_secret_key": "cfsk_secret_xyz",
            "cashfree_mode": "test",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "cashfree"
    assert body["cashfree"]["connected"] is True
    assert body["cashfree"]["app_id_masked"] == "…3249"

    g = await client.get("/api/v1/dashboard/payments/config", headers=headers)
    assert g.status_code == 200
    gb = g.json()
    assert gb["cashfree"]["connected"] is True
    # Secrets never leave the server.
    assert "cfsk_secret_xyz" not in g.text
    assert "TEST1129753249" not in g.text.replace("…3249", "")

    # Stored encrypted at rest.
    res = await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))
    cfg = res.scalars().first()
    await db_session.refresh(cfg)
    assert cfg.cashfree_app_id_enc != "TEST1129753249"
    assert cfg.cashfree_secret_enc != "cfsk_secret_xyz"


async def test_cashfree_test_connection_ok(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.services.cashfree import client as cf_client

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "order_not_found", "message": "nope"})

    cf_client.set_http_client_factory(
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        org = await _org(db_session)
        headers = await _user_client(client, db_session, org)
        await client.put(
            "/api/v1/dashboard/payments/config",
            json={
                "provider": "cashfree",
                "cashfree_app_id": "TESTID",
                "cashfree_secret_key": "SECRET",
                "cashfree_mode": "test",
            },
            headers=headers,
        )
        r = await client.post("/api/v1/dashboard/payments/cashfree/test", headers=headers)
    finally:
        cf_client.set_http_client_factory(None)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


async def test_order_uses_org_keys_not_platform(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings
    from app.services.cashfree import client as cf_client

    monkeypatch.setattr(settings, "CASHFREE_ENABLED", True)
    monkeypatch.setattr(settings, "CASHFREE_APP_ID", "PLATFORM_ID")
    monkeypatch.setattr(settings, "CASHFREE_SECRET_KEY", "PLATFORM_SECRET")

    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["client_id"] = request.headers.get("x-client-id")
        return httpx.Response(
            200,
            json={"order_id": "cf_org_1", "payment_session_id": "ps_org_1", "order_status": "ACTIVE"},
        )

    cf_client.set_http_client_factory(
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        org = await _org(db_session)
        headers = await _user_client(client, db_session, org)
        await client.put(
            "/api/v1/dashboard/payments/config",
            json={
                "provider": "cashfree",
                "cashfree_app_id": "ORG_TEST_ID",
                "cashfree_secret_key": "ORG_SECRET",
                "cashfree_mode": "test",
            },
            headers=headers,
        )
        sess = ChatSession(
            org_id=org.id,
            state_data={"name": "N", "email": "n@t.com", "phone": "+911234567890"},
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(sess)
        await db_session.commit()
        await db_session.refresh(sess)
        r = await client.post(
            "/api/v1/widget/payment/cashfree/order",
            json={"session_id": str(sess.id), "api_key": org.api_key},
        )
    finally:
        cf_client.set_http_client_factory(None)
    assert r.status_code == 200, r.text
    # Org's own keys used — NOT the platform keys.
    assert seen["client_id"] == "ORG_TEST_ID"


async def test_disconnect_clears_keys(
    client: AsyncClient, db_session: AsyncSession
):
    org = await _org(db_session)
    headers = await _user_client(client, db_session, org)
    await client.put(
        "/api/v1/dashboard/payments/config",
        json={
            "provider": "cashfree",
            "cashfree_app_id": "TESTID",
            "cashfree_secret_key": "SECRET",
            "cashfree_mode": "test",
        },
        headers=headers,
    )
    r = await client.delete("/api/v1/dashboard/payments/cashfree", headers=headers)
    assert r.status_code == 200
    assert r.json()["cashfree"]["connected"] is False
    assert r.json()["provider"] == "razorpay"
