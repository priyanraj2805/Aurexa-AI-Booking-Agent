"""Phase 1: org-mode foundation stays backward compatible."""

import sys

sys.path.insert(0, "backend")

from app.core.org_mode import (
    effective_booking_window,
    effective_slot_duration,
    effective_timezone,
    is_organization,
    validate_org_type,
)
from app.schemas.auth import UserRegister


class FakeOrg:
    def __init__(self, t=None):
        self.type = t


class FakeProvider:
    def __init__(self, timezone=None, slot=None, window=None):
        self.timezone = timezone
        self.slot_duration_minutes = slot
        self.booking_window_days = window


def test_validate_org_type_defaults_individual():
    assert validate_org_type(None) == "individual"
    assert validate_org_type("weird") == "individual"
    assert validate_org_type("organization") == "organization"


def test_is_organization():
    assert is_organization(FakeOrg("organization")) is True
    assert is_organization(FakeOrg("individual")) is False
    assert is_organization(FakeOrg(None)) is False
    assert is_organization(object()) is False


def test_effective_values_fallback_to_org():
    p = FakeProvider()
    assert effective_timezone(p, "Asia/Kolkata") == "Asia/Kolkata"
    assert effective_slot_duration(p, 60) == 60
    assert effective_booking_window(p, 60) == 60
    assert effective_timezone(None, "UTC") == "UTC"


def test_effective_values_provider_override():
    p = FakeProvider(timezone="Asia/Dubai", slot=20, window=30)
    assert effective_timezone(p, "Asia/Kolkata") == "Asia/Dubai"
    assert effective_slot_duration(p, 60) == 20
    assert effective_booking_window(p, 60) == 30


def test_register_schema_org_type():
    base = dict(
        full_name="Owner Name",
        org_name="CityCare",
        email="o@h.com",
        password="secret123",
        otp_code="123456",
    )
    assert UserRegister(**base).org_type == "individual"
    assert UserRegister(**base, org_type="organization").org_type == "organization"
