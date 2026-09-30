"""Regression: the Meta inbound webhook must authenticate its caller.

`POST /api/v1/whatsapp/webhook` processed whatever body it received. The only
check was `metadata.phone_number_id` against a configured value, which lives
INSIDE the attacker-controlled body -- so anyone who could reach the endpoint
could post a forged `messages` event and drive a booking turn (collect
details, send an OTP, create a booking) for any tenant.

The fix verifies Meta's `X-Hub-Signature-256` HMAC over the RAW body using the
App Secret, in constant time, failing closed.
"""

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import whatsapp_webhook as wh
from app.core.config import settings

SECRET = "test-app-secret-abc123"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload() -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "0",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1555000", "phone_number_id": "PNID"},
                        },
                    }
                ],
            }
        ],
    }


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(wh.router, prefix="/api/v1/whatsapp")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _app_secret(monkeypatch):
    monkeypatch.setattr(settings, "META_WHATSAPP_APP_SECRET", SECRET, raising=False)


# ── the pure verifier ──────────────────────────────────────────────────────
def test_valid_signature_accepted() -> None:
    body = json.dumps(_payload()).encode()
    assert wh.is_valid_meta_signature(SECRET, body, _sign(body)) is True


def test_invalid_signature_rejected() -> None:
    body = json.dumps(_payload()).encode()
    assert wh.is_valid_meta_signature(SECRET, body, _sign(body, "wrong-secret")) is False


def test_missing_signature_rejected() -> None:
    body = json.dumps(_payload()).encode()
    assert wh.is_valid_meta_signature(SECRET, body, None) is False
    assert wh.is_valid_meta_signature(SECRET, body, "") is False


def test_malformed_signature_rejected() -> None:
    body = json.dumps(_payload()).encode()
    for bad in ("not-a-signature", "sha256=", "sha1=" + "a" * 64, "md5=abc", "sha256=zz"):
        assert wh.is_valid_meta_signature(SECRET, body, bad) is False


def test_missing_app_secret_fails_closed() -> None:
    """No secret means nothing to verify against, so the answer must be "no" --
    even when the caller sends a well-formed digest."""
    body = json.dumps(_payload()).encode()
    assert wh.is_valid_meta_signature("", body, _sign(body)) is False
    assert wh.is_valid_meta_signature("", body, "") is False


def test_signature_is_over_raw_body_not_reserialised_json() -> None:
    """A re-serialised body must not validate, proving the raw bytes are used."""
    raw = json.dumps(_payload(), separators=(",", ":")).encode()
    reordered = json.dumps(_payload(), indent=4).encode()
    assert wh.is_valid_meta_signature(SECRET, raw, _sign(raw)) is True
    assert wh.is_valid_meta_signature(SECRET, reordered, _sign(raw)) is False


def test_compute_matches_meta_format() -> None:
    body = b'{"a":1}'
    assert wh.compute_meta_signature(SECRET, body) == _sign(body)
    assert wh.compute_meta_signature(SECRET, body).startswith("sha256=")


# ── the HTTP surface ───────────────────────────────────────────────────────
def test_post_without_signature_is_forbidden() -> None:
    r = _client().post("/api/v1/whatsapp/webhook", json=_payload())
    assert r.status_code == 403
    assert r.json()["status"] == "rejected"


def test_post_with_bad_signature_is_forbidden() -> None:
    body = json.dumps(_payload()).encode()
    r = _client().post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={wh.META_SIGNATURE_HEADER: _sign(body, "nope")},
    )
    assert r.status_code == 403


def test_post_with_malformed_signature_is_forbidden() -> None:
    r = _client().post(
        "/api/v1/whatsapp/webhook",
        content=json.dumps(_payload()).encode(),
        headers={wh.META_SIGNATURE_HEADER: "garbage"},
    )
    assert r.status_code == 403


def test_missing_app_secret_config_reports_its_own_reason(monkeypatch) -> None:
    monkeypatch.setattr(settings, "META_WHATSAPP_APP_SECRET", "", raising=False)
    r = _client().post("/api/v1/whatsapp/webhook", json=_payload())
    assert r.status_code == 403
    assert r.json()["reason"] == "app_secret_not_configured"


def test_validly_signed_webhook_still_reaches_normalisation() -> None:
    """The guard must not short-circuit the real pipeline: a correctly signed
    request still gets normalised and answered, not rejected."""
    body = json.dumps(_payload()).encode()
    r = _client().post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={wh.META_SIGNATURE_HEADER: _sign(body)},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["provider"] == "meta_cloud"
    # A status-only / non-message change is normalised and ignored, not rejected.
    assert payload["status"] == "ok"


def test_get_verification_handshake_is_untouched(monkeypatch) -> None:
    """The GET handshake must keep working: it has no signature by design."""
    monkeypatch.setattr(settings, "META_WHATSAPP_WEBHOOK_VERIFY_TOKEN", "verify-me", raising=False)
    r = _client().get(
        "/api/v1/whatsapp/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"},
    )
    assert r.status_code == 200
    assert r.text == "12345"
