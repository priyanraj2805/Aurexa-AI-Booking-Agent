"""Sliding-window sender rate limit (WhatsApp flood protection)."""
from app.core import whatsapp_debouncer as deb


def _reset():
    deb._sender_message_times.clear()


def test_steady_pacing_never_trips():
    _reset()
    # 30 messages, one every 10s: only 6 inside any 60s window — never limited.
    t = 1000.0
    for _ in range(30):
        limited, _ = deb.check_sender_rate_limit("user-a", now=t)
        assert limited is False
        t += 10.0


def test_burst_trips_then_recovers():
    _reset()
    t = 2000.0
    for _ in range(10):
        limited, _ = deb.check_sender_rate_limit("user-b", now=t)
        assert limited is False
        t += 1.0
    limited, retry_after = deb.check_sender_rate_limit("user-b", now=t)
    assert limited is True
    assert 0 < retry_after <= 60.0
    # After the window slides past the first hit, traffic flows again.
    limited, _ = deb.check_sender_rate_limit("user-b", now=t + 61.0)
    assert limited is False


def test_old_rolling_counter_scenario_stays_clean():
    """Regression: the exact 15466cf3 pattern (10 msgs over ~2min, then more).

    The old TTL cache tripped on the 11th message and silenced the user.
    Sliding window sees <=6 in any 60s slice — no limit.
    """
    _reset()
    stamps = [0, 12, 30, 47, 62, 71, 84, 94, 108, 124, 131, 135, 151]
    for i, dt in enumerate(stamps):
        limited, _ = deb.check_sender_rate_limit("user-c", now=3000.0 + dt)
        assert limited is False, f"tripped on message {i + 1}"


def test_senders_are_isolated():
    _reset()
    t = 4000.0
    for _ in range(10):
        deb.check_sender_rate_limit("busy", now=t)
        t += 1.0
    limited, _ = deb.check_sender_rate_limit("busy", now=t)
    assert limited is True
    limited, _ = deb.check_sender_rate_limit("quiet", now=t)
    assert limited is False


def test_prune_clears_expired_windows():
    _reset()
    deb.check_sender_rate_limit("gone", now=5000.0)
    assert deb.prune_rate_limit_state(now=5000.0 + 61.0) == 1
    assert "gone" not in deb._sender_message_times
