"""Internal bridge authentication (Super Admin → main backend).

The bridge lets the Super Admin control plane perform lifecycle writes on
tenant data (suspend/restore org, disable user, ...) WITHOUT exposing tenant
credentials. Every request is:

  1. Time-boxed: X-Bridge-Timestamp must be within ±MAX_SKEW_SECONDS.
  2. Signed:    X-Bridge-Signature = HMAC-SHA256(secret, canonical) hex,
                canonical = "{timestamp}.{method}.{path}.{admin_email}.{sha256(body)}".
  3. Constant-time compared (hmac.compare_digest).

When INTERNAL_BRIDGE_ENABLED=false the dependency rejects with 404 so the
surface is inert even if the router were accidentally registered.
"""
import hashlib
import hmac
import time

from fastapi import Depends, HTTPException, Request

from app.core.config import settings


class BridgeAuth:
    """Verified bridge request context."""

    def __init__(self, admin_email: str, timestamp: int) -> None:
        self.admin_email = admin_email
        self.timestamp = timestamp


def _bridge_disabled_response() -> HTTPException:
    # 503 (not 404) so callers can tell "bridge is off" apart from a genuine
    # resource-not-found coming from a bridge endpoint handler.
    return HTTPException(status_code=503, detail="Internal bridge is disabled")


def _signing_payload(request: Request, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    return ".".join(
        [
            request.headers.get("x-bridge-timestamp", ""),
            request.method,
            request.url.path,
            request.headers.get("x-bridge-admin-email", ""),
            digest,
        ]
    )


async def verify_bridge(
    request: Request, _: dict = Depends(lambda: None)
) -> BridgeAuth:
    """Validate the HMAC bridge signature, or 404 when the bridge is off."""
    if not settings.INTERNAL_BRIDGE_ENABLED:
        raise _bridge_disabled_response()
    if not settings.INTERNAL_BRIDGE_SECRET:
        raise _bridge_disabled_response()

    timestamp_raw = request.headers.get("x-bridge-timestamp")
    signature = request.headers.get("x-bridge-signature")
    admin_email = request.headers.get("x-bridge-admin-email")

    if not timestamp_raw or not signature or not admin_email:
        raise HTTPException(status_code=401, detail="Missing bridge credentials")

    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid bridge timestamp")

    now = time.time()
    if abs(now - timestamp) > settings.INTERNAL_BRIDGE_MAX_SKEW_SECONDS:
        raise HTTPException(status_code=401, detail="Bridge timestamp expired")

    body = await request.body()
    expected = hmac.new(
        settings.INTERNAL_BRIDGE_SECRET.encode(),
        _signing_payload(request, body).encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid bridge signature")

    return BridgeAuth(admin_email=admin_email, timestamp=timestamp)


def bridge_sign(
    secret: str,
    *,
    timestamp: int,
    method: str,
    path: str,
    admin_email: str,
    body: bytes = b"",
) -> str:
    """Client-side helper: produce the signature for a bridge request."""
    digest = hashlib.sha256(body).hexdigest()
    canonical = ".".join([str(timestamp), method, path, admin_email, digest])
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
