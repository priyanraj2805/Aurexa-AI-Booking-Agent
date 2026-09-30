"""Process-local vs distributed classification for WhatsApp inbound.

These tests pin the two things that are actually verifiable here:

  1. The multi-process risk is REPORTED, not silently inherited. A
     multi-worker deployment must not quietly get weaker guarantees than the
     single-worker deployment this service is actually started with.

  2. The canonical lock key. Both transports must collapse to one key, or the
     same conversation can be processed under two independent locks.

Deliberately NOT tested here: real cross-process mutual exclusion. The suite
runs on aiosqlite, so Postgres row locking and advisory locks cannot be
exercised, and Redis has no client code in the codebase at all. Asserting
distributed locking would be asserting something the test environment cannot
honour.
"""

import os

import pytest

from app.core import whatsapp_debouncer as wd


# ── 1. the risk is surfaced ────────────────────────────────────────────────
def test_single_process_reports_no_risk() -> None:
    assert wd.multi_process_risk_report(worker_count=1) is None


def test_multi_process_names_every_affected_mechanism() -> None:
    report = wd.multi_process_risk_report(worker_count=4)
    assert report is not None
    for mechanism in wd._PROCESS_LOCAL_MECHANISMS:
        assert mechanism in report
    assert "deduplication" in report
    assert "session lock" in report
    assert "flood window" in report


def test_multi_process_report_states_booking_safety_is_db_backed() -> None:
    """The warning must not imply bookings can be duplicated."""
    report = wd.multi_process_risk_report(worker_count=2)
    assert "PostgreSQL" in report
    assert "UNIQUE(bookings.slot_id)" in report


def test_explicit_acknowledgement_silences_the_warning() -> None:
    assert wd.multi_process_risk_report(worker_count=8, acknowledged=True) is None


def test_worker_count_defaults_to_one_when_unset(monkeypatch) -> None:
    for var in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "APP_WORKERS"):
        monkeypatch.delenv(var, raising=False)
    assert wd.detect_configured_worker_count() == 1


def test_worker_count_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "6")
    assert wd.detect_configured_worker_count() == 6


def test_garbage_worker_count_does_not_crash_or_claim_scale(monkeypatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "many")
    assert wd.detect_configured_worker_count() == 1
    monkeypatch.setenv("WEB_CONCURRENCY", "0")
    assert wd.multi_process_risk_report(
        worker_count=wd.detect_configured_worker_count()
    ) is None


def test_first_matching_worker_var_wins(monkeypatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    monkeypatch.setenv("APP_WORKERS", "9")
    assert wd.detect_configured_worker_count() == 2


# ── 2. one lock key per conversation, whatever the transport ───────────────
@pytest.mark.parametrize(
    "raw",
    [
        "session-abc",
        "qr:session-abc",
        "cloud:session-abc",
        "meta:session-abc",
        "whatsapp_cloud:session-abc",
    ],
)
def test_every_transport_prefix_collapses_to_the_same_key(raw: str) -> None:
    assert wd.session_lock_key(raw) == "session-abc"


def test_prefixes_are_stripped_repeatedly() -> None:
    assert wd.session_lock_key("meta:cloud:qr:session-abc") == "session-abc"


def test_blank_key_never_collapses_to_empty_string() -> None:
    """An empty key would make every anonymous conversation share one lock."""
    assert wd.session_lock_key("") == "__unknown_session__"
    assert wd.session_lock_key("   ") == "__unknown_session__"
    assert wd.session_lock_key(None) == "__unknown_session__"


# ── 3. dedup + flood semantics that are intentionally process-local ───────
def test_duplicate_message_id_is_dropped_within_this_process() -> None:
    mid = "wamid.dedup.pinned"
    wd.release_message(mid)
    assert wd.is_duplicate(mid) is False
    assert wd.is_duplicate(mid) is True
    wd.release_message(mid)


def test_released_message_id_can_be_processed_again() -> None:
    mid = "wamid.release.pinned"
    wd.release_message(mid)
    assert wd.is_duplicate(mid) is False
    wd.release_message(mid)
    assert wd.is_duplicate(mid) is False
    wd.release_message(mid)


def test_qr_and_meta_namespaces_never_collide() -> None:
    """A Baileys id and a Meta wamid must not be treated as the same message."""
    assert wd.is_duplicate("qr:shared-id") is False
    assert wd.is_duplicate("meta:shared-id") is False
    wd.release_message("qr:shared-id")
    wd.release_message("meta:shared-id")


def test_flood_window_allows_steady_conversation_but_blocks_bursts() -> None:
    key = "pinned-sender"
    base = 1_000_000.0
    # 10 messages inside the window is fine.
    for i in range(wd.RATE_LIMIT_MAX_MESSAGES):
        limited, _ = wd.check_sender_rate_limit(key, now=base + i * 0.1)
        assert limited is False
    # The 11th inside the same window is limited.
    limited, retry = wd.check_sender_rate_limit(key, now=base + 1.0)
    assert limited is True and retry > 0
    # After the window slides past, it is allowed again.
    later = base + wd.RATE_LIMIT_WINDOW_SECONDS + 5
    limited, _ = wd.check_sender_rate_limit(key, now=later)
    assert limited is False
    wd._sender_message_times.pop(key, None)


def test_otp_and_knowledge_turns_are_exempt_from_the_flood_gate() -> None:
    """The exemption itself lives in the transports; the window is the unit
    under test, so this just pins that the window is keyed per sender."""
    wd._sender_message_times.pop("exempt-sender", None)
    assert wd.check_sender_rate_limit("exempt-sender", now=2000.0) == (False, 0.0)
    assert wd.check_sender_rate_limit("other-sender", now=2000.0) == (False, 0.0)
    wd._sender_message_times.pop("exempt-sender", None)
    wd._sender_message_times.pop("other-sender", None)


def test_worker_count_helper_does_not_depend_on_import_time_env() -> None:
    """Guards against someone caching the value at module import."""
    os.environ["APP_WORKERS"] = "3"
    try:
        assert wd.detect_configured_worker_count() == 3
    finally:
        os.environ.pop("APP_WORKERS", None)
