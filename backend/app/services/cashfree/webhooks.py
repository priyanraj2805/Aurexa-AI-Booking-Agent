"""Cashfree PG webhook signature verification.

Cashfree signs PG webhooks with HMAC-SHA256 (base64) computed over
``timestamp + raw_body`` using the account **secret key**, sent as the
``x-webhook-signature`` header with ``x-webhook-timestamp``.

Security notes (same discipline as other webhook handlers here):
- Verification uses the secret key only — the oldest ACTIVE key pair if
  multiple exist (Cashfree signs with the oldest active pair).
- Always verify against the RAW request body, never re-serialized JSON.
- Reject on missing/empty signature, timestamp skew, or mismatch.
  Fail closed: unverified events must never mark payments paid.
"""

import base64
import hashlib
import hmac
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Webhook timestamps older/newer than this are rejected (replay protection).
MAX_TIMESTAMP_SKEW_SECONDS = 300


def compute_signature(secret_key: str, timestamp: str, raw_body: bytes) -> str:
    """Compute the expected x-webhook-signature value."""
    signed_payload = f"{timestamp}".encode("utf-8") + (raw_body or b"")
    digest = hmac.new(secret_key.encode("utf-8"), signed_payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_webhook_signature(
    signature: Optional[str],
    raw_body: bytes,
    timestamp: Optional[str],
    secret_key: str,
    *,
    max_skew_seconds: int = MAX_TIMESTAMP_SKEW_SECONDS,
) -> bool:
    """Return True only when the webhook signature is valid and fresh."""
    if not signature or not timestamp or not secret_key:
        return False
    try:
        ts = int(str(timestamp).strip())
    except (TypeError, ValueError):
        logger.warning("cashfree webhook rejected: bad timestamp")
        return False
    now = int(time.time())
    if abs(now - ts) > max_skew_seconds:
        logger.warning("cashfree webhook rejected: stale timestamp skew=%s", abs(now - ts))
        return False
    try:
        expected = compute_signature(secret_key, str(timestamp).strip(), raw_body or b"")
    except Exception:
        logger.warning("cashfree webhook rejected: signing error")
        return False
    if not hmac.compare_digest(expected, signature.strip()):
        logger.warning("cashfree webhook rejected: signature mismatch")
        return False
    return True
