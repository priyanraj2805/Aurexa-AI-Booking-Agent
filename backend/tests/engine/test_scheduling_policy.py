from datetime import date, datetime, timezone

import pytest

try:
    from app.engine.scheduling_policy import (
        evaluate_same_day_policy,
        evaluate_slot_cutoff_policy,
        resolve_effective_timezone_policy,
    )
except Exception:
    def resolve_effective_timezone_policy(*args, **kwargs):
        raise NotImplementedError("scheduling policy module not implemented")

    def evaluate_same_day_policy(*args, **kwargs):
        raise NotImplementedError("scheduling policy module not implemented")

    def evaluate_slot_cutoff_policy(*args, **kwargs):
        raise NotImplementedError("scheduling policy module not implemented")


def test_resolve_effective_timezone_uses_detected_timezone_when_valid():
    result = resolve_effective_timezone_policy(
        requested_tz="Asia/Kolkata",
        org_tz="UTC",
        requested_source="detected",
    )

    assert result["effective_timezone"] == "Asia/Kolkata"
    assert result["timezone_source"] == "detected"
    assert result["fallback_used"] is False
    assert result["fallback_reason"] is None


def test_resolve_effective_timezone_falls_back_for_missing_timezone():
    result = resolve_effective_timezone_policy(
        requested_tz=None,
        org_tz="America/New_York",
        requested_source="detected",
    )

    assert result["effective_timezone"] == "America/New_York"
    assert result["timezone_source"] == "fallback_org"
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "missing_tz"


def test_resolve_effective_timezone_falls_back_for_invalid_timezone():
    result = resolve_effective_timezone_policy(
        requested_tz="Mars/Olympus",
        org_tz="America/Chicago",
        requested_source="user_selected",
    )

    assert result["effective_timezone"] == "America/Chicago"
    assert result["timezone_source"] == "fallback_org"
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "invalid_tz"


def test_same_day_is_rejected_when_policy_disabled():
    now_utc = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    result = evaluate_same_day_policy(
        target_date=date(2026, 6, 1),
        now_utc=now_utc,
        effective_timezone="America/New_York",
        allow_same_day_booking=False,
    )

    assert result["allowed"] is False
    assert result["reason"] == "same_day_disabled"


def test_slot_cutoff_rejects_slots_inside_default_five_minute_buffer():
    now_utc = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
    slot_start_utc = datetime(2026, 6, 1, 14, 4, tzinfo=timezone.utc)
    result = evaluate_slot_cutoff_policy(
        slot_start_utc=slot_start_utc,
        now_utc=now_utc,
        effective_timezone="UTC",
        slot_cutoff_minutes=None,
    )

    assert result["allowed"] is False
    assert result["reason"] == "past_time_cutoff"
    assert result["cutoff_minutes"] == 5


def test_slot_cutoff_handles_dst_boundary_with_timezone_aware_datetimes():
    # US/Eastern spring forward date in 2026. 06:58 UTC is 01:58 local, and
    # 07:01 UTC maps to 03:01 local (02:00-02:59 local does not exist).
    now_utc = datetime(2026, 3, 8, 6, 58, tzinfo=timezone.utc)
    slot_start_utc = datetime(2026, 3, 8, 7, 1, tzinfo=timezone.utc)
    result = evaluate_slot_cutoff_policy(
        slot_start_utc=slot_start_utc,
        now_utc=now_utc,
        effective_timezone="America/New_York",
        slot_cutoff_minutes=5,
    )

    assert result["allowed"] is True
    assert result["reason"] is None
