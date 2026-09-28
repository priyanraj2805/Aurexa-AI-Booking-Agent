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
    looks_like_decline,
    record_offtopic,
    strike_count,
)


# â”€â”€ the counter â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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


# â”€â”€ company questions are never punished â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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


# â”€â”€ the message â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_cooldown_is_fifteen_minutes() -> None:
    """Requested change: the pause is 15 minutes, not 15 seconds."""
    assert COOLDOWN_SECONDS == 900


def test_opening_and_ongoing_messages_are_distinct() -> None:
    """The turn that OPENS the cooldown says it plainly, with no "still".
    Every later turn says "still", so the customer can tell new from ongoing."""
    from app.engine.offtopic_cooldown import cooldown_opening_message

    opening = cooldown_opening_message()
    assert "still" not in opening.lower()
    assert "cooldown" in opening.lower()
    assert "15 minutes" in opening

    ongoing = cooldown_message(899)
    assert "still" in ongoing.lower()
    assert "cooldown" in ongoing.lower()


def test_cooldown_message_states_the_wait_in_minutes() -> None:
    # A raw seconds count on a 15 minute pause reads like a malfunction.
    assert "15 minutes" in cooldown_message(900)
    assert "13 minutes" in cooldown_message(780)
    assert "about a minute" in cooldown_message(30)


def test_cooldown_message_never_goes_negative() -> None:
    for bad in (0, -5, None):
        msg = cooldown_message(bad)
        assert "cooldown" in msg.lower()
        assert "in about a minute" in msg


# â”€â”€ decline detection: the shapes the model actually produces â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.parametrize(
    "reply",
    [
        "I didn't quite catch that one, but no problem â€” we can keep going. "
        "Please share your full name.",
        "To get back to booking your appointment, could you please share your "
        "full name? I'm a booking assistant, so I can only help with "
        "appointments.",
        "I'm a booking assistant, so I can only help with appointments â€” "
        "I don't have information about that.",
        "I can help you book an appointment though. Could you please share "
        "your full name?",
        "Sorry, I don't have that information.",
    ],
)
def test_real_declines_are_detected(reply: str) -> None:
    """These are the exact shapes observed in production.

    An earlier version compared the reply to one canonical string, so two of
    these three recorded no strike at all, the counter never reached three,
    and the cooldown never opened.
    """
    assert looks_like_decline(reply) is True


@pytest.mark.parametrize(
    "reply",
    [
        "Hi there! To get started, could you please share your full name?",
        "Sure! Please share your email address.",
        "Could you please provide your phone number?",
        "Got it. What date would you like to book?",
        "What time would you like to book?",
    ],
)
def test_ordinary_booking_progress_is_not_a_decline(reply: str) -> None:
    """A reply that moves the booking forward must never be counted.

    These were being read as refusals, which struck genuine customers
    mid-booking: a single company question was enough to register a strike.
    """
    assert looks_like_decline(reply) is False


@pytest.mark.parametrize(
    "reply",
    [
        "Yes, we do. Dotstark offers dedicated Kentico developers for custom "
        "development, upgrades, and integrations. Would you like to book?",
        "We offer Azure Development Services plus Kentico CMS.",
        "We specialize in Azure Development Services. We also provide "
        "Dynamics 365 Field Service expertise.",
    ],
)
def test_real_knowledge_answers_are_not_declines(reply: str) -> None:
    """A correct answer almost always trails 'Would you like to book?'.

    Judging the booking wording first discarded a correct Kentico answer and
    left the customer one question away from an unwanted cooldown.
    """
    assert looks_like_decline(reply) is False

