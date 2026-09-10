"""Razorpay Route service package (provider payouts).

Booking payments are created on the platform account and split to providers
via Route transfers. Everything here uses **platform**
``RAZORPAY_KEY_ID``/``RAZORPAY_KEY_SECRET`` Basic Auth only.

API shapes verified against current official docs (TEST mode):
https://razorpay.com/docs/api/payments/route/
"""

from app.services.razorpay.client import RouteAPIError, RouteClient
from app.services.razorpay.commission import build_idempotency_key, calculate_split

__all__ = [
    "RouteAPIError",
    "RouteClient",
    "build_idempotency_key",
    "calculate_split",
]
