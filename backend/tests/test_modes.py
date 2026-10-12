"""Widget conversation modes (ask | booking) — guard-rail unit tests."""

from types import SimpleNamespace

import pytest

from app.schemas.chat import ChatMessageRequest
from app.engine.langgraph_orchestrator import (
    _BOOKING_DIRECTIVES,
    _session_mode,
    should_run_booking_gate,
)


def _session(mode=None):
    cd = {} if mode is None else {"_mode": mode}
    return SimpleNamespace(collected_data=cd)


def test_mode_schema_accepts_ask_booking_and_none():
    assert ChatMessageRequest(message="hi", mode="ask").mode == "ask"
    assert ChatMessageRequest(message="hi", mode="BOOKING").mode == "booking"
    assert ChatMessageRequest(message="hi").mode is None


def test_mode_schema_rejects_unknown():
    with pytest.raises(Exception):
        ChatMessageRequest(message="hi", mode="party")


def test_session_mode_reads_collected_data():
    assert _session_mode(_session("ask")) == "ask"
    assert _session_mode(_session("booking")) == "booking"
    assert _session_mode(_session()) is None
    assert _session_mode(_session("nonsense")) is None
    assert _session_mode(SimpleNamespace()) is None


def test_booking_gate_never_runs_in_ask_mode():
    assert (
        should_run_booking_gate(
            directive="PROCESS_BOOKING",
            missing_fields=[],
            require_otp=True,
            require_phone_otp=False,
            otp_verified=True,
            phone_otp_verified=False,
            user_confirmed=True,
            mode="ask",
        )
        is False
    )


def test_booking_gate_unchanged_without_mode():
    # OTP satisfied + confirmed + nothing missing => gate runs (legacy).
    assert (
        should_run_booking_gate(
            directive="PROCESS_BOOKING",
            missing_fields=[],
            require_otp=True,
            require_phone_otp=False,
            otp_verified=True,
            phone_otp_verified=False,
            user_confirmed=True,
        )
        is True
    )


def test_booking_directives_cover_collection_and_confirm():
    for d in (
        "REQUIRE_DATE_PICKER",
        "REQUIRE_TIME_SLOT_GRID",
        "REQUIRE_OTP_MODAL",
        "REQUEST_CONFIRMATION",
        "PROCESS_BOOKING",
    ):
        assert d in _BOOKING_DIRECTIVES
    assert "TEXT_INPUT" not in _BOOKING_DIRECTIVES


def test_mode_prompt_hint_routes_by_mode():
    from app.engine.agent.graph import _mode_prompt_hint

    ask = _mode_prompt_hint("ask")
    assert "ASK AI" in ask
    assert "Book Appointment button" in ask
    assert "We dont have information regarding this." in ask
    booking = _mode_prompt_hint("booking")
    assert "GUIDED BOOKING" in booking
    assert _mode_prompt_hint(None) == ""
    assert _mode_prompt_hint("party") == ""
