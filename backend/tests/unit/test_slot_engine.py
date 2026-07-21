import pytest
from datetime import date, time, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.engine.slot_engine import compute_available_slots, _slots_for_range


class TestSlotsForRange:
    def test_generates_correct_slots(self):
        slots = _slots_for_range(
            open_t=time(9, 0),
            close_t=time(17, 0),
            duration_minutes=60,
            break_start=None,
            break_end=None,
        )
        assert time(9, 0) in slots
        assert time(10, 0) in slots
        assert time(16, 0) in slots
        assert time(17, 0) not in slots

    def test_respects_break_window(self):
        slots = _slots_for_range(
            open_t=time(9, 0),
            close_t=time(17, 0),
            duration_minutes=60,
            break_start=time(12, 0),
            break_end=time(13, 0),
        )
        assert time(12, 0) not in slots
        assert time(11, 0) in slots
        assert time(13, 0) in slots


class MockOrgConfig:
    def __init__(self, active_provider=None):
        self.active_calendar_provider = active_provider


class MockProvider:
    def __init__(self, name, busy_periods=None):
        self.provider_name = name
        self._busy_periods = busy_periods or []

    async def is_connected(self, config):
        return True

    async def get_busy_periods(self, config, start_time, end_time, db=None):
        return self._busy_periods


def create_mock_result(value):
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    if isinstance(value, list):
        mock_scalars.all.return_value = value
    else:
        mock_scalars.first.return_value = value
    mock_result.scalars.return_value = mock_scalars
    return mock_result


class TestComputeAvailableSlots:
    @pytest.mark.asyncio
    async def test_no_schedule_returns_empty(self):
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=create_mock_result(None))

        result = await compute_available_slots(mock_db, uuid4(), date.today())
        assert result == []

    @pytest.mark.asyncio
    async def test_inactive_day_returns_empty(self):
        schedule = MagicMock()
        schedule.slot_duration_minutes = 60

        day = MagicMock()
        day.is_active = False

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            side_effect=[
                create_mock_result(schedule),
                create_mock_result(day),
            ]
        )

        result = await compute_available_slots(mock_db, uuid4(), date.today())
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_booked_slots(self):
        schedule = MagicMock()
        schedule.slot_duration_minutes = 60

        day = MagicMock()
        day.is_active = True
        day.open_time = time(9, 0)
        day.close_time = time(17, 0)
        day.break_start = None
        day.break_end = None

        booked_slot = MagicMock()
        booked_slot.is_booked = True
        booked_slot.start_time = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
        booked_slot.locked_until = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            side_effect=[
                create_mock_result(schedule),
                create_mock_result(day),
                create_mock_result(None),
                create_mock_result([booked_slot]),
            ]
        )

        result = await compute_available_slots(mock_db, uuid4(), date(2026, 3, 27))
        assert time(10, 0) not in result
        assert time(9, 0) in result

    @pytest.mark.asyncio
    async def test_computes_slots_in_business_timezone_then_returns_utc_times(self):
        """Asia/Kolkata 09:00 business slot should map to 03:30 UTC."""
        org_id = uuid4()

        schedule = MagicMock()
        schedule.slot_duration_minutes = 90
        schedule.timezone = "Asia/Kolkata"

        day = MagicMock()
        day.is_active = True
        day.open_time = time(9, 0)
        day.close_time = time(17, 0)
        day.break_start = None
        day.break_end = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            side_effect=[
                create_mock_result(schedule),
                create_mock_result(day),
                create_mock_result(None),
                create_mock_result([]),
            ]
        )

        result = await compute_available_slots(mock_db, org_id, date(2026, 4, 3))

        # 09:00 IST -> 03:30 UTC, 10:30 IST -> 05:00 UTC
        assert time(3, 30) in result
        assert time(5, 0) in result
        assert time(9, 0) not in result


class TestCalendarConflictDetection:
    @pytest.mark.asyncio
    async def test_filters_google_busy_periods(self):
        from app.models.config import CalendarProviderType
        from app.models.booking import ExceptionType

        schedule = MagicMock()
        schedule.slot_duration_minutes = 60

        day = MagicMock()
        day.is_active = True
        day.open_time = time(9, 0)
        day.close_time = time(17, 0)
        day.break_start = None
        day.break_end = None

        mock_exception = MagicMock()
        mock_exception.exception_type = None  # Not CLOSED or CUSTOM_HOURS

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            side_effect=[
                create_mock_result(schedule),
                create_mock_result(day),
                create_mock_result(mock_exception),
                create_mock_result(MockOrgConfig(CalendarProviderType.GOOGLE)),
                create_mock_result([]),
            ]
        )

        busy_period = (
            datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 27, 11, 0, tzinfo=timezone.utc),
        )

        with patch("app.engine.slot_engine.get_provider") as mock_get_provider:
            mock_provider = MockProvider("google", [busy_period])
            mock_get_provider.return_value = mock_provider

            result = await compute_available_slots(
                mock_db,
                uuid4(),
                date(2026, 3, 27),
                org_config=MockOrgConfig(CalendarProviderType.GOOGLE),
            )

            assert time(10, 0) not in result
            assert time(9, 0) in result
            assert time(14, 0) in result

    @pytest.mark.asyncio
    async def test_none_provider_skips_external_calendar(self):
        from app.models.config import CalendarProviderType

        schedule = MagicMock()
        schedule.slot_duration_minutes = 60

        day = MagicMock()
        day.is_active = True
        day.open_time = time(9, 0)
        day.close_time = time(17, 0)
        day.break_start = None
        day.break_end = None

        mock_exception = MagicMock()
        mock_exception.exception_type = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            side_effect=[
                create_mock_result(schedule),
                create_mock_result(day),
                create_mock_result(mock_exception),
                create_mock_result(MockOrgConfig(CalendarProviderType.NONE)),
                create_mock_result([]),
            ]
        )

        with patch("app.engine.slot_engine.get_provider") as mock_get_provider:
            mock_provider = MockProvider(
                "google",
                [
                    (
                        datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
                        datetime(2026, 3, 27, 11, 0, tzinfo=timezone.utc),
                    )
                ],
            )
            mock_get_provider.return_value = mock_provider

            result = await compute_available_slots(
                mock_db,
                uuid4(),
                date(2026, 3, 27),
                org_config=MockOrgConfig(CalendarProviderType.NONE),
            )

            assert time(10, 0) in result

    @pytest.mark.asyncio
    async def test_unconnected_provider_skips_external(self):
        from app.models.config import CalendarProviderType

        schedule = MagicMock()
        schedule.slot_duration_minutes = 60

        day = MagicMock()
        day.is_active = True
        day.open_time = time(9, 0)
        day.close_time = time(17, 0)
        day.break_start = None
        day.break_end = None

        mock_exception = MagicMock()
        mock_exception.exception_type = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            side_effect=[
                create_mock_result(schedule),
                create_mock_result(day),
                create_mock_result(mock_exception),
                create_mock_result(MockOrgConfig(CalendarProviderType.GOOGLE)),
                create_mock_result([]),
            ]
        )

        with patch("app.engine.slot_engine.get_provider") as mock_get_provider:
            mock_provider = MockProvider("google")
            mock_provider.is_connected = AsyncMock(return_value=False)
            mock_get_provider.return_value = mock_provider

            result = await compute_available_slots(
                mock_db,
                uuid4(),
                date(2026, 3, 27),
                org_config=MockOrgConfig(CalendarProviderType.GOOGLE),
            )

            assert time(10, 0) in result

    @pytest.mark.asyncio
    async def test_unconnected_provider_skips_external(self):
        from app.models.config import CalendarProviderType

        schedule = MagicMock()
        schedule.slot_duration_minutes = 60

        day = MagicMock()
        day.is_active = True
        day.open_time = time(9, 0)
        day.close_time = time(17, 0)
        day.break_start = None
        day.break_end = None

        mock_exception = MagicMock()
        mock_exception.exception_type = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            side_effect=[
                create_mock_result(schedule),
                create_mock_result(day),
                create_mock_result(mock_exception),
                create_mock_result(MockOrgConfig(CalendarProviderType.GOOGLE)),
                create_mock_result([]),
            ]
        )

        with patch("app.engine.slot_engine.get_provider") as mock_get_provider:
            mock_provider = MockProvider("google")
            mock_provider.is_connected = AsyncMock(return_value=False)
            mock_get_provider.return_value = mock_provider

            result = await compute_available_slots(
                mock_db,
                uuid4(),
                date(2026, 3, 27),
                org_config=MockOrgConfig(CalendarProviderType.GOOGLE),
            )

            assert time(10, 0) in result
