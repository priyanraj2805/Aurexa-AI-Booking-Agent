"""Razorpay Route operations (platform credentials only).

Endpoint/field shapes verified against current official docs (TEST mode):
- Linked Accounts .... POST/GET/PATCH /v2/accounts[/{id}]
  https://razorpay.com/docs/api/payments/route/create-linked-account
- Stakeholders ........ POST /v2/accounts/{id}/stakeholders
  https://razorpay.com/docs/api/payments/route/create-stakeholder
- Product config ...... POST /v2/accounts/{id}/products
                        {product_name: "route", tnc_accepted: true}
  PATCH (settlement) .. /v2/accounts/{id}/products/{product_id}
                        {settlements: {account_number, ifsc_code,
                         beneficiary_name}, tnc_accepted: true}
  https://razorpay.com/docs/api/payments/route/request-product-config
  https://razorpay.com/docs/api/payments/route/update-product-config
- Transfers ........... POST /v1/payments/{id}/transfers
                        {transfers: [{account, amount, currency, notes?}]}
  Reconcile ........... GET /v1/payments/{id}/transfers
                        GET /v1/transfers/{id}
  https://razorpay.com/docs/api/payments/route/create-transfers-payments
  https://razorpay.com/docs/api/payments/route/fetch-transfers-payment
- Reversal ............ POST /v1/transfers/{id}/reversals {amount?, notes?}
  Refund+reversal ..... POST /v1/payments/{id}/refund {amount, reverse_all}
  (reverse_all valid for full refunds and single-account partial refunds;
  multi-account partial refunds require the reversal API instead.)
  https://razorpay.com/docs/api/payments/route/refund-payments-and-reverse-transfer
  https://razorpay.com/docs/api/payments/route/reverse-a-transfer
- Webhooks ............ transfer.processed / transfers.failed /
                        settlement.processed
  https://razorpay.com/docs/payments/route/subscribe-to-webhooks

Nothing here is invented: unknown/new fields returned by Razorpay are passed
through untouched; required request fields match the docs above.

Security: bank account numbers and KYC values are NEVER logged. Safe helpers
return only last4/beneficiary metadata for persistence.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from app.services.razorpay.client import RouteClient

logger = logging.getLogger(__name__)

CURRENCY_INR = "INR"
# Docs error: account number must be 5-20 chars (product update) / 5-35 (account).
_ACCOUNT_RE = re.compile(r"^\d{5,35}$")
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$", re.IGNORECASE)


# ── Safe helpers (no sensitive data in logs/returns) ─────────────────────

def bank_last4(account_number: str) -> str:
    digits = re.sub(r"\D", "", account_number or "")
    if len(digits) < 4:
        raise ValueError("Invalid bank account number")
    return digits[-4:]


def validate_settlement_inputs(account_number: str, ifsc_code: str, beneficiary_name: str) -> None:
    if not account_number or not _ACCOUNT_RE.match(re.sub(r"\s", "", account_number)):
        raise ValueError("Invalid bank account number (must be 5-35 digits)")
    if not ifsc_code or not _IFSC_RE.match(ifsc_code.strip()):
        raise ValueError("Invalid IFSC code")
    if not beneficiary_name or len(beneficiary_name.strip()) < 2:
        raise ValueError("Invalid beneficiary name")


def _safe_account_log(account_id: Optional[str]) -> str:
    return str(account_id or "?")[:18]


# ── Linked Accounts ──────────────────────────────────────────────────────

def build_linked_account_payload(
    *,
    email: str,
    phone: str,
    reference_id: str,
    legal_business_name: str,
    business_type: str = "proprietorship",
    contact_name: Optional[str] = None,
    customer_facing_business_name: Optional[str] = None,
    category: str = "healthcare",
    subcategory: str = "clinic",
    business_model: str = "Appointment booking services",
) -> Dict[str, Any]:
    """Build POST /v2/accounts payload with documented fields only."""
    if not email or not phone or not reference_id or not legal_business_name:
        raise ValueError("email, phone, reference_id and legal_business_name are required")
    return {
        "email": email,
        "phone": phone,
        "type": "route",
        "reference_id": reference_id,
        "legal_business_name": legal_business_name,
        "business_type": business_type,
        "contact_name": contact_name or email.split("@")[0],
        "customer_facing_business_name": customer_facing_business_name or legal_business_name,
        "profile": {
            "category": category,
            "subcategory": subcategory,
            "business_model": business_model,
        },
        "notes": {"aurexa": "true", "reference_id": reference_id},
    }


async def create_linked_account(client: RouteClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    data = await client.post("/v2/accounts", payload)
    logger.info("route linked account created id=%s", _safe_account_log(data.get("id")))
    return data


async def fetch_linked_account(client: RouteClient, account_id: str) -> Dict[str, Any]:
    return await client.get(f"/v2/accounts/{account_id}")


async def update_linked_account(
    client: RouteClient, account_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    data = await client.patch(f"/v2/accounts/{account_id}", payload)
    logger.info("route linked account updated id=%s", _safe_account_log(account_id))
    return data


# ── Stakeholders ─────────────────────────────────────────────────────────

def build_stakeholder_payload(
    *,
    name: str,
    email: str,
    street: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    postal_code: Optional[str] = None,
    country: str = "IN",
    pan: Optional[str] = None,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build POST .../stakeholders payload with documented fields only."""
    if not name or not email:
        raise ValueError("stakeholder name and email are required")
    payload: Dict[str, Any] = {"name": name, "email": email}
    if street or city or state or postal_code:
        payload["addresses"] = {
            "residential": {
                "street": street or "",
                "city": city or "",
                "state": state or "",
                "postal_code": postal_code or "",
                "country": country,
            }
        }
    if pan:
        payload["kyc"] = {"pan": pan}
    payload["notes"] = {"aurexa": "true"}
    if org_id:
        payload["notes"]["org_id"] = str(org_id)
    return payload


async def create_stakeholder(
    client: RouteClient, account_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    data = await client.post(f"/v2/accounts/{account_id}/stakeholders", payload)
    logger.info("route stakeholder created account=%s", _safe_account_log(account_id))
    return data


# ── Route product configuration ──────────────────────────────────────────

async def request_route_product(client: RouteClient, account_id: str, *, tnc_accepted: bool) -> Dict[str, Any]:
    """POST /v2/accounts/{id}/products. tnc_accepted=True ONLY after real UI acceptance."""
    if tnc_accepted is not True:
        raise ValueError("tnc_accepted must be true (provider must accept terms in the UI first)")
    data = await client.post(
        f"/v2/accounts/{account_id}/products",
        {"product_name": "route", "tnc_accepted": True},
    )
    logger.info(
        "route product requested account=%s product=%s status=%s",
        _safe_account_log(account_id), str(data.get("id", "?"))[:20], data.get("activation_status"),
    )
    return data


async def fetch_product_config(client: RouteClient, account_id: str, product_id: str) -> Dict[str, Any]:
    return await client.get(f"/v2/accounts/{account_id}/products/{product_id}")


async def update_product_settlement(
    client: RouteClient,
    account_id: str,
    product_id: str,
    *,
    account_number: str,
    ifsc_code: str,
    beneficiary_name: str,
    tnc_accepted: bool = True,
) -> Dict[str, Any]:
    """PATCH settlement details. Returns Razorpay response; caller persists last4 only."""
    validate_settlement_inputs(account_number, ifsc_code, beneficiary_name)
    if tnc_accepted is not True:
        raise ValueError("tnc_accepted must be true for settlement updates")
    data = await client.patch(
        f"/v2/accounts/{account_id}/products/{product_id}",
        {
            "settlements": {
                "account_number": re.sub(r"\s", "", account_number),
                "ifsc_code": ifsc_code.strip().upper(),
                "beneficiary_name": beneficiary_name.strip(),
            },
            "tnc_accepted": True,
        },
    )
    # Log contains IDs/status only — never account numbers or IFSC.
    logger.info(
        "route product settlement updated account=%s product=%s status=%s",
        _safe_account_log(account_id), str(product_id)[:20], data.get("activation_status"),
    )
    return data


def summarize_settlement_for_storage(beneficiary_name: str, account_number: str) -> Dict[str, str]:
    """Safe metadata for DB persistence (no raw account number stored)."""
    return {"bank_last4": bank_last4(account_number), "beneficiary_name": beneficiary_name.strip()[:255]}


# ── Platform orders (Route payments are created here, never on sub-merchants) ──

async def create_platform_order(
    client: RouteClient,
    *,
    amount_paise: int,
    receipt: str,
    currency: str = CURRENCY_INR,
    notes: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """POST /v1/orders on the PLATFORM account (Basic Auth platform keys).

    Same standard Orders endpoint the legacy flow uses — but authenticated as
    the platform, so captured payments can fund Route transfers.
    """
    if currency != CURRENCY_INR:
        raise ValueError("Route booking payments support INR only")
    if not isinstance(amount_paise, int) or amount_paise < 100:
        raise ValueError("amount_paise must be an integer >= 100")
    if not receipt:
        raise ValueError("receipt is required")
    payload: Dict[str, Any] = {"amount": amount_paise, "currency": currency, "receipt": receipt}
    if notes:
        payload["notes"] = dict(notes)
    data = await client.post("/v1/orders", payload)
    if not data.get("id"):
        raise ValueError("Razorpay did not return an order id")
    logger.info("route platform order created receipt=%s", receipt[:40])
    return data


# ── Transfers from captured payments ─────────────────────────────────────

async def create_transfer_from_payment(
    client: RouteClient,
    payment_id: str,
    *,
    account_id: str,
    amount_paise: int,
    currency: str = CURRENCY_INR,
    notes: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """POST /v1/payments/{id}/transfers — single provider transfer.

    Only captured INR payments; amount >= 100 paise (>= INR 1 per docs).
    Total of all transfers on a payment must not exceed the captured amount
    (enforced by the orchestration layer, which reconciles first).
    """
    if not payment_id or not account_id:
        raise ValueError("payment_id and account_id are required")
    if currency != CURRENCY_INR:
        raise ValueError("Route transfers support INR only")
    if not isinstance(amount_paise, int) or amount_paise < 100:
        raise ValueError("amount_paise must be an integer >= 100")
    entry: Dict[str, Any] = {"account": account_id, "amount": amount_paise, "currency": currency}
    if notes:
        entry["notes"] = dict(notes)
    data = await client.post(f"/v1/payments/{payment_id}/transfers", {"transfers": [entry]})
    items = data.get("items") or []
    logger.info(
        "route transfer created payment=%s account=%s count=%s",
        str(payment_id)[:16], _safe_account_log(account_id), len(items),
    )
    return data


async def fetch_transfers_for_payment(client: RouteClient, payment_id: str) -> List[Dict[str, Any]]:
    """GET /v1/payments/{id}/transfers — reconciliation source of truth."""
    data = await client.get(f"/v1/payments/{payment_id}/transfers")
    items = data.get("items") or []
    return items if isinstance(items, list) else []


async def fetch_transfer(client: RouteClient, transfer_id: str) -> Dict[str, Any]:
    return await client.get(f"/v1/transfers/{transfer_id}")


def find_transfer_for_account(transfers: List[Dict[str, Any]], account_id: str) -> Optional[Dict[str, Any]]:
    for item in transfers:
        if item.get("recipient") == account_id:
            return item
    return None


# ── Reversals / refunds (verified shapes; partial multi-account NOT implemented) ──

async def reverse_transfer(
    client: RouteClient,
    transfer_id: str,
    *,
    amount_paise: Optional[int] = None,
    notes: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """POST /v1/transfers/{id}/reversals. Omit amount to reverse in full."""
    payload: Dict[str, Any] = {}
    if amount_paise is not None:
        if not isinstance(amount_paise, int) or amount_paise < 100:
            raise ValueError("amount_paise must be an integer >= 100")
        payload["amount"] = amount_paise
    if notes:
        payload["notes"] = dict(notes)
    data = await client.post(f"/v1/transfers/{transfer_id}/reversals", payload)
    logger.info("route transfer reversed id=%s", str(transfer_id)[:16])
    return data


async def refund_payment_with_reversal(
    client: RouteClient,
    payment_id: str,
    *,
    amount_paise: int,
    reverse_all: bool = True,
) -> Dict[str, Any]:
    """POST /v1/payments/{id}/refund {amount, reverse_all}.

    Per docs, reverse_all covers full refunds and single-account partial
    refunds. Partial refunds across MULTIPLE linked accounts are NOT
    implemented here (use reverse_transfer explicitly instead).
    """
    if not isinstance(amount_paise, int) or amount_paise < 100:
        raise ValueError("amount_paise must be an integer >= 100")
    data = await client.post(
        f"/v1/payments/{payment_id}/refund",
        {"amount": amount_paise, "reverse_all": bool(reverse_all)},
    )
    logger.info("route payment refund issued payment=%s reverse_all=%s", str(payment_id)[:16], reverse_all)
    return data
