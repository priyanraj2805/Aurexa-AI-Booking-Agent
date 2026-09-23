"""HTTP client for the wa-gateway sidecar (Baileys transport).

The backend owns organization ownership and connection metadata; the gateway
owns sockets and QR material. Auth is a shared service key (X-Gateway-Key),
mirroring the bridge pattern. Timeouts are short so a wedged gateway fails
fast into 502s instead of hanging admin requests.

Security: the service key is never logged; responses carry only connection
ids, statuses, QR data URLs and phone metadata — never auth state.
"""

import logging
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


class WaGatewayError(Exception):
    """Gateway unreachable or rejected the call. Message is safe for API responses."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _base_url() -> str:
    return (settings.WA_GATEWAY_BASE_URL or "http://wa-gateway:3100").rstrip("/")


def _headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Gateway-Key": settings.WA_GATEWAY_SERVICE_KEY or "",
    }


async def _request(
    method: str, path: str, payload: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Returns parsed JSON, or None on gateway 404 (unknown session / no QR)."""
    url = f"{_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
            resp = await http.request(method, url, headers=_headers(), json=payload)
    except httpx.HTTPError as exc:
        logger.warning("wa-gateway %s %s unreachable: %s", method, path, type(exc).__name__)
        raise WaGatewayError("WhatsApp gateway is unreachable") from exc
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        logger.warning("wa-gateway %s %s failed status=%s", method, path, resp.status_code)
        raise WaGatewayError("WhatsApp gateway request failed", status_code=resp.status_code)
    try:
        data = resp.json()
    except Exception as exc:
        raise WaGatewayError("WhatsApp gateway returned an invalid response") from exc
    return data if isinstance(data, dict) else {}


async def start_session(connection_id: str) -> Dict[str, Any]:
    data = await _request("POST", f"/sessions/{connection_id}/start")
    if data is None:  # pragma: no cover — gateway always answers start
        raise WaGatewayError("WhatsApp gateway did not start the session")
    return data


async def get_qr(connection_id: str) -> Optional[Dict[str, Any]]:
    return await _request("GET", f"/sessions/{connection_id}/qr")


async def get_session_status(connection_id: str) -> Optional[Dict[str, Any]]:
    return await _request("GET", f"/sessions/{connection_id}/status")


async def logout_session(connection_id: str) -> Dict[str, Any]:
    data = await _request("DELETE", f"/sessions/{connection_id}")
    return data or {"connectionId": connection_id, "status": "logged_out"}


async def reconnect_session(connection_id: str) -> Optional[Dict[str, Any]]:
    return await _request("POST", f"/sessions/{connection_id}/reconnect")


async def send_session_message(
    connection_id: str, to: str, body: str, idem_key: Optional[str] = None
) -> Dict[str, Any]:
    """Admin-initiated push (human handover) to a customer chat.

    Raises WaGatewayError when the socket is offline (409) or unreachable.
    Never logs message content.
    """
    payload: Dict[str, Any] = {"to": to, "body": body[:4096]}
    if idem_key:
        payload["idemKey"] = idem_key
    data = await _request("POST", f"/sessions/{connection_id}/send", payload)
    return data or {"connectionId": connection_id, "sent": True}
