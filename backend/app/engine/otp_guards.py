"""OTP verify-turn guards (pure, dependency-light for testability).

Covers the screenshot regression: a 6-digit code turn must attempt
verification whenever an OTP is outstanding — even if the turn snapshot
lost the collected phone — and a successful phone verification must
backfill the normalized number so "verified but missing" is impossible.
"""

from typing import Dict, List


def resolve_otp_verify_channels(
    *,
    is_whatsapp: bool,
    require_phone_otp: bool,
    require_otp: bool,
    phone_verified: bool,
    email_verified: bool,
    collected_phone: str,
    otp_was_sent: bool,
) -> List[str]:
    """Decide which OTP channels to attempt for a 6-digit code turn."""
    phone_pending = bool(require_phone_otp) and not bool(phone_verified) and (
        bool((collected_phone or "").strip()) or bool(otp_was_sent)
    )
    email_pending = bool(require_otp) and not bool(email_verified)
    if phone_pending and email_pending:
        return ["phone", "email"] if is_whatsapp else ["email", "phone"]
    if phone_pending:
        return ["phone"]
    if email_pending:
        return ["email"]
    return []


def apply_verified_phone(collected: Dict, verified_phone: str) -> Dict:
    """Backfill the verified destination into collected data (normalized).

    A valid verified number is stored E.164-normalized; garbage never
    overwrites a good existing value.
    """
    from app.engine.validators import DataValidator

    out = dict(collected or {})
    try:
        normalized, error, _meta = DataValidator._validate_phone(
            str(verified_phone or "")
        )
    except Exception:
        normalized, error = None, "error"
    if normalized and not error:
        out["phone"] = normalized
    return out
