"""Regression: a decline and a question in the same message.

Reported symptom: the customer sent

    "I don't want to book appointment. Can you tell me about the services
     provided by dotstark"

and received only

    "No problem. That's completely okay. If you'd like to book in the future,
     feel free to reach out anytime."

The second sentence was never answered.

`is_no_booking_request` matches on the FIRST clause and the orchestrator's
decline gate returned immediately, so the whole message was treated as a bare
refusal. The refusal is about BOOKING; the question is about the BUSINESS, and
they are independent -- so the question must be answered on its own merits.
"""

import pytest

from app.engine import decline_flow


# ── the exact reported message ─────────────────────────────────────────────
def test_reported_message_is_both_a_decline_and_a_question() -> None:
    msg = "I don't want to book appointment. Can you tell me about the services provided by dotstark"
    assert decline_flow.is_no_booking_request(msg) is True
    assert decline_flow.carries_knowledge_question(msg) is True


@pytest.mark.parametrize(
    "msg",
    [
        "I don't want to book. What services do you offer?",
        "I don't want to book. Tell me about your services.",
        "I don't want to book. How much does it cost?",
        "I don't want to book. What are your opening hours?",
        "No thanks. Can you tell me about the services?",
        "I'm not interested. What do you actually do?",
        "changed my mind. Do you offer physiotherapy?",
    ],
)
def test_decline_plus_question_is_detected(msg: str) -> None:
    assert decline_flow.carries_knowledge_question(msg) is True


# ── a PURE decline must still take the graceful decline path ───────────────
@pytest.mark.parametrize(
    "msg",
    [
        "I don't want to book",
        "I don't want to book an appointment",
        "no",
        "no thanks",
        "nah",
        "not interested",
        "changed my mind",
        "just kidding",
        "I don't want to book, ok?",
        "no, don't want to book",
        "",
        "   ",
    ],
)
def test_pure_declines_carry_no_question(msg: str) -> None:
    assert decline_flow.carries_knowledge_question(msg) is False


def test_standalone_no_is_never_treated_as_a_question() -> None:
    assert decline_flow.carries_knowledge_question("no") is False
    assert decline_flow.carries_knowledge_question("no thanks") is False
    # Even with elongation and casing.
    assert decline_flow.carries_knowledge_question("NOOO thanks") is False


# ── a question with no decline in it is not this helper's business ─────────
@pytest.mark.parametrize(
    "msg",
    [
        "What services do you offer?",
        "can you tell me about dotstark",
        "tell me about the services",
    ],
)
def test_bare_questions_are_unaffected_by_the_decline_gate(msg: str) -> None:
    """These never reach the decline gate, so the helper must not matter."""
    assert decline_flow.is_no_booking_request(msg) is False


# ── the two decisions are independent and must not both fire ──────────────
@pytest.mark.parametrize(
    "msg",
    [
        "I don't want to book appointment. Can you tell me about the services provided by dotstark",
        "I don't want to book. What are your opening hours?",
    ],
)
def test_exactly_one_of_decline_shortcut_or_question_routing_is_chosen(msg: str) -> None:
    """The gate is `decline and not carries_question`, so a mixed turn must
    NOT take the canned-decline shortcut -- that was the bug."""
    take_decline_shortcut = (
        decline_flow.is_no_booking_request(msg)
        and not decline_flow.carries_knowledge_question(msg)
    )
    assert take_decline_shortcut is False


def test_cancellation_is_still_owned_by_the_cancel_flow() -> None:
    """A cancel turn must never be reclassified as a decline-with-question."""
    assert decline_flow.is_no_booking_request("cancel my booking") is False


# -- the engine is handed only the question --------------------------------
def test_question_part_extracts_the_question_from_the_reported_message() -> None:
    msg = "I don't want to book appointment. Can you tell me about the services provided by dotstark"
    q = decline_flow.question_part(msg)
    assert q
    assert "book" not in q.lower()
    assert "services" in q.lower()


@pytest.mark.parametrize(
    ("msg", "expected_contains"),
    [
        ("I don't want to book. What services do you offer?", "services"),
        ("no thanks. Tell me about your services.", "services"),
        ("changed my mind. Do you offer physiotherapy?", "physiotherapy"),
    ],
)
def test_question_part_keeps_the_question(msg: str, expected_contains: str) -> None:
    q = decline_flow.question_part(msg)
    assert expected_contains in q.lower()


@pytest.mark.parametrize(
    "msg",
    ["I don't want to book", "no thanks", "not interested", "changed my mind", "", "ok"],
)
def test_question_part_is_empty_for_a_pure_decline(msg: str) -> None:
    assert decline_flow.question_part(msg) == ""


def test_question_part_never_raises_on_odd_input() -> None:
    for junk in (None, 123, "....", "?!?!", "  .  "):
        assert isinstance(decline_flow.question_part(junk), str)


def test_full_booking_decline_is_preserved_in_the_transcript_not_rewritten() -> None:
    """The rewrite applies to the ENGINE input only. The transcript keeps the
    customer's actual words, otherwise the inbox would show a message they
    never sent."""
    msg = "I don't want to book appointment. Can you tell me about the services"
    q = decline_flow.question_part(msg)
    assert q != msg
    assert "I don't want to book" in msg, "the original must be available for the transcript"
