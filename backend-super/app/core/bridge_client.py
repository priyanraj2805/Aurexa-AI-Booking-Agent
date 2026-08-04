"""Client for the main backend's internal bridge (HMAC-signed lifecycle writes).

Mirrors the signing scheme in the main backend (app.core.bridge):
  canonical = "{timestamp}.{method}.{path}.{admin_email}.{sha256(body)}"
  signature = hex(HMAC-SHA256(secret, canonical))

The main backend 503s every bridge call while INTERNAL_BRIDGE_ENABLED=false
(its default), which surfaces to the UI as a clean "bridge disabled" error.
"""
import hashlib
import hmac
import logging
import time
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

BRIDGE_PATH = "/api/v1/internal/bridge"


class BridgeError(HTTPException):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)


def _sign(method: str, path: str, admin_email: str, body: bytes) -> tuple[str, str]:
    """Sign using the FULL bridge path — the main backend's verify_bridge
    recomputes the HMAC over request.url.path, which includes the
    /api/v1/internal/bridge prefix."""
    ts = str(int(time.time()))
    digest = hashlib.sha256(body).hexdigest()
    canonical = ".".join([ts, method, BRIDGE_PATH + path, admin_email, digest])
    sig = hmac.new(
        settings.INTERNAL_BRIDGE_SECRET.encode(),
        canonical.encode(),
        hashlib.sha256,
    ).hexdigest()
    return ts, sig


async def bridge_call(
    method: str,
    path: str,
    admin_email: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform an HMAC-signed request against the main backend bridge."""
    url = settings.INTERNAL_API_URL.rstrip("/") + BRIDGE_PATH + path
    body = b""
    if json_body is not None:
        import json

        body = json.dumps(json_body, separators=(",", ":")).encode()

    ts, sig = _sign(method, path, admin_email, body)
    headers = {
        "X-Bridge-Timestamp": ts,
        "X-Bridge-Signature": sig,
        "X-Bridge-Admin-Email": admin_email,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(
                method, url, content=body or None, headers=headers
            )
    except httpx.HTTPError as exc:
        logger.warning("Bridge call failed: %s %s (%s)", method, path, exc)
        raise BridgeError(502, "Internal bridge unreachable")

    if response.status_code == 503:
        raise BridgeError(
            503,
            "Bridge is disabled — set INTERNAL_BRIDGE_ENABLED=true on the main backend",
        )
    if response.status_code >= 400:
        detail = "Bridge error"
        try:
            data = response.json()
            detail = data.get("detail", detail) if isinstance(data, dict) else detail
        except Exception:
            pass
        raise BridgeError(response.status_code, str(detail))

    return response.json()


def lifecycle_paths() -> dict[str, str]:
    """Endpoint path templates keyed by action name."""
    return {
        "org.suspend": "/orgs/{org_id}/suspend",
        "org.restore": "/orgs/{org_id}/restore",
        "org.delete": "/orgs/{org_id}/delete",
        "org.create": "/orgs",
        "org.config": "/orgs/{org_id}/config",
        "user.disable": "/users/{user_id}/disable",
        "user.enable": "/users/{user_id}/enable",
    }
