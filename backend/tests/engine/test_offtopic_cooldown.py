"""Off-topic cooldown guardrail.

Three off-topic questions in a row pause the session for 15 seconds. While
paused, any message gets one short line and nothing else is processed. A real
booking step or a real knowledge answer clears the counter, so a customer who
comes back to actually book is never punished for earlier questions.
"""
import time

import pytest

from app.engine.offtopic_cooldown import (
    COOLDOWN_SECONDS,
    STRIKE_LIMIT,
    clear,
    cooldown_message,
    cooldown_remaining,
    is_cooling_down,
    looks_like_asking_about_us,
    record_offtopic,
    strike_count,
)


# ── the counter ────────────────────────────────────────────────────────

def test_no_cooldown_on_a_fresh_session() -> None:
    data: dict = {}
    assert strike_count(data) == 0
    assert is_cooling_down(data) is False
    assert cooldown_remaining(data) == 0


def test_strikes_accumulate_up_to_the_limit() -> None:
    data: dict = {}
    for expected in range(1, STRIKE_LIMIT):
        assert record_offtopic(data) == expected
        assert is_cooling_down(data) is False
    # The strike that hits the limit opens the window.
    assert record_offtopic(data) == STRIKE_LIMIT
    assert is_cooling_down(data) is True


def test_cooldown_lasts_fifteen_seconds() -> None:
    data: dict = {}
    for _ in range(STRIKE_LIMIT):
        record_offtopic(data)
    remaining = cooldown_remaining(data)
    assert 0 < remaining <= COOLDOWN_SECONDS


def test_cooldown_expires_on_its_own() -> None:
    """No background job exists, so an elapsed window must simply stop
    blocking. Otherwise a session would be locked out forever."""
    data: dict = {}
    for _ in range(STRIKE_LIMIT):
        record_offtopic(data)
    # Force the window into the past.
    from app.engine.offtopic_cooldown import _UNTIL_KEY

    data[_UNTIL_KEY] = time.time() - 1
    assert is_cooling_down(data) is False
    assert cooldown_remaining(data) == 0


def test_remaining_counts_down() -> None:
    data: dict = {}
    for _ in range(STRIKE_LIMIT):
        record_offtopic(data)
    from app.engine.offtopic_cooldown import _UNTIL_KEY

    data[_UNTIL_KEY] = time.time() + 5
    assert 3 <= cooldown_remaining(data) <= 6


def test_clear_resets_everything() -> None:
    data: dict = {}
    for _ in range(STRIKE_LIMIT):
        record_offtopic(data)
    assert is_cooling_down(data) is True
    clear(data)
    assert strike_count(data) == 0
    assert is_cooling_down(data) is False


def test_strikes_survive_arbitrary_session_data() -> None:
    """The counter rides along with normal session fields, so a real session
    payload must not break it."""
    data = {"name": "Ravi", "email": "r@x.com", "time": "10:30 AM"}
    record_offtopic(data)
    assert strike_count(data) == 1
    assert data["name"] == "Ravi"


def test_malformed_state_does_not_raise() -> None:
    """This runs on the inbound path; an exception here would strand a
    customer mid-booking."""
    for bad in ({"_offtopic_strike_count": "abc"}, {"_offtopic_cooldown_until": "x"}, {}):
        assert strike_count(bad) == 0
        assert cooldown_remaining(bad) == 0
        assert is_cooling_down(bad) is False


# ── company questions are never punished ───────────────────────────────

@pytest.mark.parametrize(
    "question",
    [
        "what services do you offer?",
        "how much does a website cost?",
        "tell me about your pricing",
        "do you have any discounts?",
        "what are your opening hours?",
        "where are you located?",
        "explain your maintenance plans",
        "can I speak to a human",
    ],
)
def test_genuine_company_questions_are_recognised(question: str) -> None:
    assert looks_like_asking_about_us(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "who is the prime minister of pakistan",
        "capital of France",
        "tell me a joke",
        "who won the football world cup",
        "what is 2 + 2",
        "",
    ],
)
def test_off_topic_questions_are_not_mistaken_for_company_questions(question: str) -> None:
    assert looks_like_asking_about_us(question) is False


# ── the message ────────────────────────────────────────────────────────

def test_cooldown_message_states_the_wait() -> None:
    msg = cooldown_message(12)
    assert "cooldown" in msg.lower()
    assert "12" in msg


def test_cooldown_message_never_goes_negative() -> None:
    for bad in (0, -5, None):
        msg = cooldown_message(bad)
        assert "cooldown" in msg.lower()
        assert "-" not in msg.replace("still in cooldown", "")
