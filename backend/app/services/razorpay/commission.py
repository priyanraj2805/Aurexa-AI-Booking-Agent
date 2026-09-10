"""Deterministic commission calculation (integer paise only).

No floating point anywhere. The split is computed once, persisted on the
transfer row BEFORE any Razorpay call, and recomputed identically on retry —
a mismatch aborts instead of re-sending.
"""

import hashlib
from typing import Tuple


def calculate_split(gross_paise: int, commission_bps: int) -> Tuple[int, int]:
    """Return ``(commission_paise, provider_paise)``.

    ``commission_bps`` is basis points (1000 = 10%). Integer floor division;
    any remainder paise stays with the platform (deterministic).
    """
    if not isinstance(gross_paise, int) or not isinstance(commission_bps, int):
        raise ValueError("gross_paise and commission_bps must be integers (paise)")
    if gross_paise <= 0:
        raise ValueError("gross_paise must be positive")
    if not 0 <= commission_bps <= 10000:
        raise ValueError("commission_bps must be between 0 and 10000")
    commission = (gross_paise * commission_bps) // 10000
    provider = gross_paise - commission
    if provider <= 0:
        raise ValueError("provider payout must be positive (check commission_bps)")
    return commission, provider


def build_idempotency_key(org_id: str, razorpay_payment_id: str, provider_paise: int) -> str:
    """Stable idempotency key binding org + payment + exact payout amount."""
    raw = f"{org_id}|{razorpay_payment_id}|{provider_paise}".encode()
    return "route_" + hashlib.sha256(raw).hexdigest()[:32]
