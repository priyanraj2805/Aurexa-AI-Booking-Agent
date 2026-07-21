from unittest.mock import patch

from app.core.config import settings
from app.db.database import engine, _after_cursor_execute


def test_pool_settings_wired_from_config():
    # ensure config and engine wiring are explicit and non-zero
    assert settings.DB_POOL_SIZE > 0
    assert settings.DB_MAX_OVERFLOW >= 0
    assert settings.DB_POOL_TIMEOUT > 0


def test_logs_slow_query_over_threshold():
    class DummyConn:
        def __init__(self):
            self.info = {"query_start_time": [1.0]}

    conn = DummyConn()
    with patch("time.perf_counter", return_value=3.5):
        with patch("app.db.database.logger.warning") as warn:
            _after_cursor_execute(conn, None, "SELECT 1", None, None, None)
            assert warn.call_count == 1
            args, kwargs = warn.call_args
            assert args[0] == "slow_query"
            extra = kwargs.get("extra", {})
            assert "duration_ms" in extra
            assert "statement" in extra


def test_does_not_log_under_threshold():
    class DummyConn:
        def __init__(self):
            self.info = {"query_start_time": [1.0]}

    conn = DummyConn()
    # 10ms duration
    with patch("time.perf_counter", return_value=1.01):
        with patch("app.db.database.logger.warning") as warn:
            _after_cursor_execute(conn, None, "SELECT 1", None, None, None)
            assert warn.call_count == 0

