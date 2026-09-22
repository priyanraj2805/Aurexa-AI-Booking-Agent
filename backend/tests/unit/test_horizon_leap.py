"""Leap-aware booking horizon ceiling (no hardcoded 365/366)."""
from datetime import date

from app.engine.scheduling_policy import (
    clamp_horizon_days,
    max_horizon_days,
)


def test_non_leap_year_caps_365():
    # 2026 is not leap; next leap (2028) is out of reach all year.
    assert max_horizon_days(date(2026, 9, 21)) == 365
    assert max_horizon_days(date(2026, 1, 1)) == 365
    # Early 2027 cannot reach Feb 2028 within 366 days either.
    assert max_horizon_days(date(2027, 1, 1)) == 365
    # ...but mid-2027 windows cross Feb 29 2028, so 366 is correct there.
    assert max_horizon_days(date(2027, 6, 15)) == 366


def test_leap_year_allows_366():
    # Feb 29 2024 reachable from anywhere in/before it.
    assert max_horizon_days(date(2024, 1, 15)) == 366
    assert max_horizon_days(date(2023, 12, 1)) == 366  # window reaches Feb 2024
    assert max_horizon_days(date(2028, 2, 28)) == 366


def test_leap_passed_falls_back():
    # After Feb 2024 with next leap out of reach -> 365.
    assert max_horizon_days(date(2024, 4, 1)) == 365
    # Late 2027 reaches Feb 2028 -> 366.
    assert max_horizon_days(date(2027, 12, 1)) == 366


def test_clamp_uses_dynamic_ceiling():
    assert clamp_horizon_days(400, date(2026, 9, 21)) == 365
    assert clamp_horizon_days(400, date(2024, 1, 15)) == 366
    assert clamp_horizon_days(0) == 1
    assert clamp_horizon_days(-5) == 1
    assert clamp_horizon_days("abc") == 60
    assert clamp_horizon_days(60) == 60


def test_resolver_prefers_schedule_and_clamps():
    import asyncio

    from app.engine.scheduling_policy import resolve_booking_horizon_days

    class FakeRow:
        def __init__(self, window):
            self.booking_window_days = window

    class FakeResult:
        def __init__(self, row):
            self._row = row

        def scalars(self):
            return self

        def first(self):
            return self._row

    class FakeDB:
        def __init__(self, row):
            self._row = row

        async def execute(self, *args, **kwargs):
            return FakeResult(self._row)

    async def run():
        # Schedule wins over config.
        days, source, diverged = await resolve_booking_horizon_days(
            FakeDB(FakeRow(45)), "org", type("C", (), {"max_booking_days_ahead": 90})()
        )
        assert (days, source, diverged) == (45, "schedule", True)
        # Absurd schedule value clamps to the leap-aware ceiling.
        days2, _, _ = await resolve_booking_horizon_days(
            FakeDB(FakeRow(3640)), "org", None
        )
        assert days2 <= 366
        # Missing everywhere -> default 60.
        days3, source3, _ = await resolve_booking_horizon_days(
            FakeDB(None), "org", None
        )
        assert (days3, source3) == (60, "default")

    asyncio.run(run())
