"""Low-level Cashfree PG HTTP client (platform credentials only).

Mirrors the conventions of app/services/razorpay/client.py:
- httpx with per-request auth headers (x-client-id / x-client-secret /
  x-api-version); the ``requests``-style SDK is NOT added.
- Never logs secrets, keys, or full payloads. Logs contain only HTTP
  method, path, status code, and Cashfree order/payment IDs.
- TEST (sandbox) first: base URL comes from settings (sandbox default).

Cashfree PG auth: x-client-id = App ID, x-client-secret = Secret Key,
x-api-version e.g. 2026-01-01. Webhook signatures are HMAC-SHA256
(base64) over timestamp + raw body using the secret key — see webhooks.py.
"""

import logging
from typing import Any, Callable, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20


class CashfreeAPIError(Exception):
    """Safe Cashfree API failure. Message never contains secrets."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        cashfree_code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.cashfree_code = cashfree_code


# Test hook: tests may install a factory returning an httpx.AsyncClient backed
# by a MockTransport. Production path always builds a real client.
_HTTP_CLIENT_FACTORY: Optional[Callable[[], httpx.AsyncClient]] = None


def set_http_client_factory(factory: Optional[Callable[[], httpx.AsyncClient]]) -> None:
    """Install/override the HTTP client factory (tests only)."""
    global _HTTP_CLIENT_FACTORY
    _HTTP_CLIENT_FACTORY = factory


def is_cashfree_enabled() -> bool:
    """Global kill switch. When False, no Cashfree API call may be made."""
    return bool(getattr(settings, "CASHFREE_ENABLED", False))


def require_cashfree_enabled() -> None:
    if not is_cashfree_enabled():
        raise CashfreeAPIError("Cashfree is disabled (CASHFREE_ENABLED=false)")


class CashfreeClient:
    """Thin wrapper over Cashfree PG REST endpoints (platform credentials)."""

    def __init__(
        self,
        app_id: str,
        secret_key: str,
        api_base: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not app_id or not secret_key:
            raise CashfreeAPIError("Cashfree credentials are not configured")
        self._app_id = app_id
        # Kept in memory only for the request; never logged or returned.
        self._secret_key = secret_key
        self._api_base = (api_base or settings.CASHFREE_API_BASE or "https://sandbox.cashfree.com/pg").rstrip("/")
        self._api_version = api_version or settings.CASHFREE_API_VERSION or "2026-01-01"
        self._timeout = timeout_seconds

    @property
    def api_base(self) -> str:
        """Resolved API base (sandbox or production) for this client."""
        return self._api_base

    @classmethod
    def from_settings(cls, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> "CashfreeClient":
        """Build from platform settings. Raises if Cashfree is disabled."""
        require_cashfree_enabled()
        return cls(
            app_id=settings.CASHFREE_APP_ID,
            secret_key=settings.CASHFREE_SECRET_KEY,
            api_base=settings.CASHFREE_API_BASE,
            api_version=settings.CASHFREE_API_VERSION,
            timeout_seconds=timeout_seconds,
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-client-id": self._app_id,
            "x-client-secret": self._secret_key,
            "x-api-version": self._api_version,
        }

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
        require_cashfree_enabled()
        url = f"{self._api_base}{path}"
        try:
            async with self._client() as http:
                resp = await http.request(method, url, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            # Timeout is ambiguous (Cashfree may have accepted). Caller MUST
            # reconcile via GET before retrying — never blindly re-POST.
            logger.warning("cashfree %s %s timeout (reconcile before retry)", method, path)
            raise CashfreeAPIError(f"Cashfree request timed out: {method} {path}") from exc
        except httpx.HTTPError as exc:
            logger.warning("cashfree %s %s transport error", method, path)
            raise CashfreeAPIError(f"Cashfree transport error: {method} {path}") from exc

        if resp.status_code >= 400:
            code: Optional[str] = None
            description = f"HTTP {resp.status_code}"
            try:
                err = resp.json()
                code = str(err.get("code") or err.get("type") or "")
                description = str(err.get("message") or description)[:300]
            except Exception:
                pass
            logger.warning("cashfree %s %s failed status=%s code=%s", method, path, resp.status_code, code)
            raise CashfreeAPIError(
                f"Cashfree API error: {description}",
                status_code=resp.status_code,
                cashfree_code=code,
            )
        logger.info("cashfree %s %s ok status=%s", method, path, resp.status_code)
        try:
            data = resp.json()
        except Exception as exc:
            raise CashfreeAPIError("Cashfree returned a non-JSON response") from exc
        return data if isinstance(data, dict) else {"_items": data}

    async def create_order(
        self,
        *,
        order_id: str,
        order_amount: float,
        order_currency: str = "INR",
        customer_id: str = "",
        customer_email: str = "",
        customer_phone: str = "",
        notify_url: str = "",
        return_url: str = "",
        order_note: str = "",
    ) -> Dict[str, Any]:
        """POST /pg/orders — returns order dict with payment_session_id."""
        payload: Dict[str, Any] = {
            "order_id": order_id,
            "order_amount": order_amount,
            "order_currency": order_currency,
            "customer_details": {
                "customer_id": customer_id or order_id,
                "customer_email": customer_email or "customer@example.com",
                "customer_phone": customer_phone or "9999999999",
            },
        }
        if notify_url:
            payload["order_meta"] = {"notify_url": notify_url}
            if return_url:
                payload["order_meta"]["return_url"] = return_url
        if order_note:
            payload["order_note"] = order_note
        # NOTE: api_base already ends with /pg — paths here are relative to it.
        return await self._request("POST", "/orders", payload)

    async def fetch_order(self, order_id: str) -> Dict[str, Any]:
        """GET /orders/{order_id} — reconcile status (order_status, payments)."""
        if not order_id:
            raise CashfreeAPIError("order_id is required")
        return await self._request("GET", f"/orders/{order_id}")

    async def fetch_order_payments(self, order_id: str) -> Dict[str, Any]:
        """GET /orders/{order_id}/payments — list payment attempts."""
        if not order_id:
            raise CashfreeAPIError("order_id is required")
        return await self._request("GET", f"/orders/{order_id}/payments")

    async def refund(
        self,
        *,
        order_id: str,
        refund_amount: float,
        refund_id: str = "",
        refund_note: str = "",
    ) -> Dict[str, Any]:
        """POST /orders/{order_id}/refunds."""
        if not order_id:
            raise CashfreeAPIError("order_id is required")
        payload: Dict[str, Any] = {"refund_amount": refund_amount}
        if refund_id:
            payload["refund_id"] = refund_id
        if refund_note:
            payload["refund_note"] = refund_note
        return await self._request("POST", f"/orders/{order_id}/refunds", payload)

    async def create_payment_link(
        self,
        *,
        link_id: str,
        link_amount: float,
        link_currency: str = "INR",
        link_purpose: str = "",
        customer_phone: str = "",
        customer_email: str = "",
        customer_name: str = "",
        notify_url: str = "",
        link_expiry_time: str = "",
        upi_intent: bool = True,
        link_notes: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """POST /pg/links — returns link dict with link_url.

        Used for channels without a JS checkout (e.g. WhatsApp): the
        customer opens link_url in a browser and pays there. Raises
        CashfreeAPIError when Cashfree rejects or omits link_url.
        """
        if not link_id:
            raise CashfreeAPIError("link_id is required")
        if not customer_phone:
            raise CashfreeAPIError("customer_phone is required for payment links")
        payload: Dict[str, Any] = {
            "link_id": link_id,
            "link_amount": link_amount,
            "link_currency": link_currency,
            "link_purpose": (link_purpose or "Appointment booking payment")[:500],
            "customer_details": {
                "customer_phone": customer_phone,
            },
        }
        if customer_email:
            payload["customer_details"]["customer_email"] = customer_email
        if customer_name:
            payload["customer_details"]["customer_name"] = customer_name[:100]
        payload["link_meta"] = {"upi_intent": bool(upi_intent)}
        if notify_url:
            payload["link_meta"]["notify_url"] = notify_url
        if link_expiry_time:
            payload["link_expiry_time"] = link_expiry_time
        payload["link_notify"] = {"send_sms": False, "send_email": False, "send_whatsapp": False}
        payload["link_auto_reminders"] = False
        payload["link_partial_payments"] = False
        if link_notes:
            payload["link_notes"] = {k: str(v)[:100] for k, v in list(link_notes.items())[:5]}
        # NOTE: api_base already ends with /pg — paths here are relative to it.
        resp = await self._request("POST", "/links", payload)
        if not resp.get("link_url"):
            raise CashfreeAPIError("Cashfree did not return a payment link URL")
        return resp

    async def fetch_refunds(self, order_id: str) -> Dict[str, Any]:
        """GET /orders/{order_id}/refunds — list issued refunds (idempotency check)."""
        if not order_id:
            raise CashfreeAPIError("order_id is required")
        return await self._request("GET", f"/orders/{order_id}/refunds")
