"""Organization mode helpers (individual shop vs hospital mall).

Individual orgs (type='individual') keep the old behavior:
  org-level hours/timezone, bookings without unit/provider.

Organization orgs (type='organization'):
  departments = service_units, doctors = providers,
  each doctor can override timezone / slot duration / booking window,
  bookings carry unit_id + provider_id.
"""

from typing import Optional


def is_organization(org) -> bool:
    return getattr(org, "type", "individual") == "organization"


def effective_timezone(provider, org_timezone: str) -> str:
    tz = (getattr(provider, "timezone", None) or "").strip() if provider else ""
    return tz or org_timezone or "UTC"


def effective_slot_duration(provider, org_default: int) -> int:
    if provider is not None and getattr(provider, "slot_duration_minutes", None):
        return int(provider.slot_duration_minutes)
    return int(org_default)


def effective_booking_window(provider, org_default: int) -> int:
    if provider is not None and getattr(provider, "booking_window_days", None):
        return int(provider.booking_window_days)
    return int(org_default)


def validate_org_type(value: Optional[str]) -> str:
    if value == "organization":
        return "organization"
    return "individual"
