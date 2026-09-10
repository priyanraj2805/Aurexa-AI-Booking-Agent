"""Low-level Razorpay Route HTTP client (platform credentials only).

Uses ``httpx`` with Basic Auth (``RAZORPAY_KEY_ID`` / ``RAZORPAY_KEY_SECRET``)
consistent with the existing codebase — the Razorpay Python SDK is NOT added.

Security rules enforced here:
- Never reads ``OrgConfig.razorpay_access_token_enc`` or any per-org token.
- Never logs secrets, tokens, bank numbers, or KYC payloads.
  Logs contain only HTTP method, path, status code, and Razorpay IDs
  (``acc_*`` / ``pay_*`` / ``trf_*``) as correlation identifiers.
- TEST mode first: no live activation logic in this module.
"""

import logging
from typing import Any, Callable, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20


class RouteAPIError(Exception):
    """Safe Razorpay API failure. Message never contains secrets or KYC data."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        razorpay_code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.razorpay_code = razorpay_code


# Test hook: tests may install a factory returning an httpx.AsyncClient backed
# by a MockTransport. Production path always builds a real client.
_HTTP_CLIENT_FACTORY: Optional[Callable[[], httpx.AsyncClient]] = None


def set_http_client_factory(factory: Optional[Callable[[], httpx.AsyncClient]]) -> None:
    """Install/override the HTTP client factory (tests only)."""
    global _HTTP_CLIENT_FACTORY
    _HTTP_CLIENT_FACTORY = factory


def is_route_enabled() -> bool:
    """Global kill switch. When False, no Route API call may be made."""
    return bool(getattr(settings, "RAZORPAY_ROUTE_ENABLED", False))


def require_route_enabled() -> None:
    if not is_route_enabled():
        raise RouteAPIError("Razorpay Route is disabled (RAZORPAY_ROUTE_ENABLED=false)")


class RouteClient:
    """Thin wrapper over Razorpay Route REST endpoints (platform Basic Auth)."""

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        api_base: Optional[str] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not key_id or not key_secret:
            raise RouteAPIError("Platform Razorpay credentials are not configured")
        self._key_id = key_id
        # Kept in memory only for the request; never logged or returned.
        self._key_secret = key_secret
        self._api_base = (api_base or settings.RAZORPAY_ROUTE_API_BASE or "https://api.razorpay.com").rstrip("/")
        self._timeout = timeout_seconds

    @classmethod
    def from_settings(cls, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> "RouteClient":
        """Build from platform settings. Raises if Route is disabled."""
        require_route_enabled()
        return cls(
            key_id=settings.RAZORPAY_KEY_ID,
            key_secret=settings.RAZORPAY_KEY_SECRET,
            api_base=settings.RAZORPAY_ROUTE_API_BASE,
            timeout_seconds=timeout_seconds,
        )

    def _client(self) -> httpx.AsyncClient:
        if _HTTP_CLIENT_FACTORY is not None:
            return _HTTP_CLIENT_FACTORY()
        return httpx.AsyncClient(timeout=self._timeout)

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        require_route_enabled()
        url = f"{self._api_base}{path}"
        try:
            async with self._client() as http:
                resp = await http.request(
                    method,
                    url,
                    json=payload,
                    auth=(self._key_id, self._key_secret),
                    headers={"Content-Type": "application/json"},
                )
        except httpx.TimeoutException as exc:
            # Timeout is ambiguous (Razorpay may have accepted). Caller MUST
            # reconcile via GET before retrying — never blindly re-POST.
            logger.warning("route %s %s timeout (reconcile before retry)", method, path)
            raise RouteAPIError(f"Razorpay request timed out: {method} {path}") from exc
        except httpx.HTTPError as exc:
            logger.warning("route %s %s transport error", method, path)
            raise RouteAPIError(f"Razorpay transport error: {method} {path}") from exc

        if resp.status_code >= 400:
            code: Optional[str] = None
            description = f"HTTP {resp.status_code}"
            try:
                err = (resp.json().get("error") or {})
                code = err.get("code")
                # Description may echo input; keep it but it never contains secrets
                # because we never send secrets/KYC in cleartext-relevant fields.
                description = str(err.get("description") or description)[:300]
            except Exception:
                pass
            logger.warning("route %s %s failed status=%s code=%s", method, path, resp.status_code, code)
            raise RouteAPIError(
                f"Razorpay API error: {description}",
                status_code=resp.status_code,
                razorpay_code=code,
            )
        logger.info("route %s %s ok status=%s", method, path, resp.status_code)
        try:
            data = resp.json()
        except Exception as exc:
            raise RouteAPIError("Razorpay returned a non-JSON response") from exc
        return data if isinstance(data, dict) else {"_items": data}

    async def get(self, path: str) -> Dict[str, Any]:
        return await self._request("GET", path)

    async def fetch_payment(self, payment_id: str) -> Dict[str, Any]:
        """GET /v1/payments/{id} on the platform account (verify Route payments)."""
        if not payment_id:
            raise RouteAPIError("payment_id is required")
        return await self.get(f"/v1/payments/{payment_id}")

    async def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", path, payload)

    async def patch(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("PATCH", path, payload)
