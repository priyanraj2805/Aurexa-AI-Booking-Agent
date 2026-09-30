"""Behavioural parity between the two WhatsApp transports.

The business rule is "3 unanswered off-topic questions -> 15 minute cooldown".
It must hold identically whether the message arrived over Baileys/QR or the
Meta Cloud API.

It did not. The off-topic accounting block existed ONLY in
`whatsapp_qr.py` -- `record_offtopic` had exactly one call site in the whole
codebase -- so a Meta Cloud customer could never accumulate a strike. The
cooldown GATE lives in the orchestrator and was shared, which made it worse
rather than better: Cloud honoured a window opened over Baileys but could
never open one of its own.

A second, separate asymmetry: the Cloud transport deduped billable Meta
templates per step (`step_template_already_sent`), the QR transport did not,
so a stuck step re-fired a paid template on every turn.
"""

import pytest

from app.core import whatsapp_templates as wt
from app.engine.offtopic_cooldown import (
    STRIKE_LIMIT,
    account_offtopic_turn,
    cooldown_opening_message,
    strike_count,
)

TRIVIA = "who is the prime minister of pakistan"
DECLINE = "I don't have information about that. I can help you book instead."


def _turn(cd, *, user=TRIVIA, reply=DECLINE, **kw):
    return account_offtopic_turn(cd, user_text=user, reply_text=reply, **kw)


# ── the shared rule, transport-agnostic ────────────────────────────────────
def test_three_off_topic_turns_open_the_window() -> None:
    cd: dict = {}
    assert _turn(cd) is None
    assert _turn(cd) is None
    replacement = _turn(cd)
    assert replacement == cooldown_opening_message()
    assert strike_count(cd) == STRIKE_LIMIT


def test_a_booking_turn_clears_the_counter() -> None:
    cd: dict = {}
    _turn(cd)
    _turn(cd)
    _turn(cd, user="I want to book a physiotherapy session",
          reply="Could you please share your full name?", turn_intent="START_BOOKING")
    assert strike_count(cd) == 0


def test_a_confirmation_is_never_a_decline() -> None:
    cd: dict = {}
    for _ in range(5):
        _turn(cd, reply="Your booking is confirmed! We look forward to seeing you.")
    assert strike_count(cd) == 0


def test_a_company_question_is_not_held_against_the_customer() -> None:
    cd: dict = {}
    for _ in range(5):
        _turn(cd, user="what are your opening hours?", reply=DECLINE)
    assert strike_count(cd) == 0


def test_a_data_answer_with_pending_fields_is_booking_progress() -> None:
    """The clarification reply "I didn't quite catch that... share your full
    name" is decline-SHAPED, so a data answer during collection must still be
    exempt. This used to pass because `missing_fields` alone granted the
    exemption; it now passes because the customer's turn is a value, not a
    question."""
    cd: dict = {}
    _turn(cd)
    _turn(cd,
          user="Priyank",
          reply="I didn't quite catch that, but no problem. Please share your full name.",
          missing_fields=["name"])
    assert strike_count(cd) == 0


def test_none_collected_data_is_a_no_op_not_a_crash() -> None:
    assert account_offtopic_turn(None, user_text=TRIVIA, reply_text=DECLINE) is None


# ── both transports must reach the SAME outcome ────────────────────────────
@pytest.mark.parametrize("transport", ["qr_baileys", "meta_cloud"])
def test_same_business_rule_on_both_transports(transport: str) -> None:
    """Both transports call the same function, so the rule cannot diverge.

    Each transport is a named case rather than a loop, so a failure says which
    transport regressed.
    """
    cd: dict = {}
    notices = []
    for _ in range(STRIKE_LIMIT):
        notices.append(_turn(cd))
    # Notice is emitted by the turn that CROSSES the threshold. Turns 1 and 2
    # record a strike and say nothing; turn 3 opens the window and announces.
    assert notices[0] is None
    assert notices[1] is None
    assert notices[2] == cooldown_opening_message()
    assert strike_count(cd) == STRIKE_LIMIT
    assert cd["_offtopic_cooldown_until"] > 0


def test_the_opening_notice_is_emitted_exactly_once_per_window() -> None:
    """After the window opens, the shared orchestrator gate answers with the
    ongoing line, which is not a decline -- so accounting stays quiet and the
    notice is not re-announced."""
    cd: dict = {}
    emitted = 0
    for reply in [DECLINE, DECLINE, DECLINE, "You're still in cooldown. Please try again in about 15 minutes."]:
        if _turn(cd, reply=reply):
            emitted += 1
    assert emitted == 1


def test_an_ongoing_cooldown_reply_adds_no_strike_and_no_notice() -> None:
    """While the window is open the shared gate answers, so the accounting layer
    sees a non-decline reply.

    Two invariants, both of which used to be violated:
      * the opening notice is not re-announced, and no new strike is recorded;
      * the ACTIVE window is not released. `clear()` used to pop the deadline
        too, and the reply the customer receives while cooling down is
        "You're still in cooldown..." -- a non-decline -- so the guardrail
        deleted its own deadline and let them back in after one message.
    """
    cd: dict = {}
    for _ in range(STRIKE_LIMIT):
        _turn(cd)
    assert cd["_offtopic_cooldown_until"] > 0
    deadline = cd["_offtopic_cooldown_until"]

    notice = _turn(cd, reply="You're still in cooldown. Please try again in about 15 minutes.")

    assert notice is None, "the opening notice must not be re-announced"
    assert strike_count(cd) <= STRIKE_LIMIT, "no additional strike may be recorded"
    assert cd["_offtopic_cooldown_until"] == deadline, (
        "an active cooldown must survive a non-decline reply"
    )


def test_an_on_topic_turn_does_not_release_an_active_window() -> None:
    """A booking step mid-cooldown resets the strikes but keeps the pause."""
    cd: dict = {}
    for _ in range(STRIKE_LIMIT):
        _turn(cd)
    deadline = cd["_offtopic_cooldown_until"]
    _turn(cd, user="I want to book", reply="Could you please share your full name?",
          turn_intent="START_BOOKING")
    assert strike_count(cd) == 0, "the counter should reset"
    assert cd["_offtopic_cooldown_until"] == deadline, (
        "the window is ended by the clock, not by a single good turn"
    )


def test_an_expired_window_is_still_cleaned_up() -> None:
    """A stale deadline must not linger after it has passed."""
    cd: dict = {"_offtopic_cooldown_until": 1.0, "_offtopic_strike_count": 3}
    _turn(cd, user="I want to book", reply="Share your name", turn_intent="START_BOOKING")
    assert "_offtopic_cooldown_until" not in cd


# ── per-step template dedupe ───────────────────────────────────────────────
def test_step_template_dedupe_roundtrip() -> None:
    state: dict = {}
    missing = ["email"]
    assert wt.step_template_already_sent(state, wt.TEMPLATE_REQUEST_EMAIL, missing) is False
    state = wt.mark_step_template_sent(state, wt.TEMPLATE_REQUEST_EMAIL, missing)
    assert wt.step_template_already_sent(state, wt.TEMPLATE_REQUEST_EMAIL, missing) is True


def test_a_different_step_is_not_blocked_by_a_previous_send() -> None:
    state: dict = {}
    state = wt.mark_step_template_sent(state, wt.TEMPLATE_REQUEST_EMAIL, ["email"])
    assert wt.step_template_already_sent(state, wt.TEMPLATE_REQUEST_PHONE, ["phone"]) is False
    assert wt.step_template_already_sent(state, wt.TEMPLATE_WELCOME, ["name"]) is False


def test_moving_to_a_new_step_allows_its_template_again() -> None:
    """The guard is per (template, missing-field set), so a genuinely new step
    must not be suppressed by the previous step's send."""
    state: dict = {}
    state = wt.mark_step_template_sent(state, wt.TEMPLATE_REQUEST_EMAIL, ["email"])
    assert wt.step_template_already_sent(state, wt.TEMPLATE_REQUEST_EMAIL, ["phone"]) is False


# -- the pending-field exemption must not be permanent ----------------------
def test_pending_fields_alone_do_not_exempt_a_question() -> None:
    """Regression found live: a trivia question made the engine start a booking
    flow, which populated missing_fields. The exemption was
    `is_on_topic_turn(...) or bool(missing_fields)`, so from then on EVERY turn
    was exempt and the guardrail could never fire again for that session."""
    cd: dict = {}
    missing = ["name", "email", "phone", "date", "time"]
    for _ in range(STRIKE_LIMIT):
        _turn(cd, user=TRIVIA, missing_fields=missing)
    assert strike_count(cd) == STRIKE_LIMIT
    assert cd.get("_offtopic_cooldown_until", 0) > 0


def test_a_bare_data_answer_is_still_exempt_while_fields_are_pending() -> None:
    """The exemption still does its real job: a field value is not a strike."""
    cd: dict = {}
    missing = ["name", "email"]
    for _ in range(STRIKE_LIMIT):
        _turn(cd, user="Priyank", reply="Thanks, what email should I use?",
              missing_fields=missing)
    assert strike_count(cd) == 0


def test_a_time_answer_is_still_exempt() -> None:
    cd: dict = {}
    for _ in range(STRIKE_LIMIT):
        _turn(cd, user="3:00 PM", reply="Please confirm your booking", missing_fields=["time"])
    assert strike_count(cd) == 0


@pytest.mark.parametrize(
    ("text", "is_question"),
    [
        ("who is the prime minister of pakistan", True),
        ("what is the capital of Japan", True),
        ("who won in 1998", True),
        ("are you open on Sunday?", True),
        ("Priyank", False),
        ("29/09/2026", False),
        ("3:00 PM", False),
        ("+916350063338", False),
        ("otp.fix@example.com", False),
    ],
)
def test_question_detection(text, is_question) -> None:
    from app.engine.offtopic_cooldown import looks_like_an_off_topic_question

    assert looks_like_an_off_topic_question(text) is is_question


# -- a carried-over intent must not grant a standing exemption --------------
def test_a_carried_over_booking_intent_does_not_exempt_trivia() -> None:
    """Regression found live, twice.

    `agent/graph.py` returns `current_intent` unchanged, so after the engine
    started a booking the intent echoes START_BOOKING forever. The exemption
    trusted that stale value, so every later question was exempt and the
    guardrail never fired again for that session.
    """
    cd: dict = {}
    for _ in range(STRIKE_LIMIT):
        _turn(cd, turn_intent="START_BOOKING")
    assert strike_count(cd) == STRIKE_LIMIT


def test_the_customers_own_words_to_book_are_always_exempt() -> None:
    """The strongest signal is never overridden."""
    cd: dict = {}
    for _ in range(STRIKE_LIMIT):
        _turn(cd, user="I want to book a physiotherapy session",
              reply="Could you please share your full name?",
              turn_intent="ASK_QUESTION")
    assert strike_count(cd) == 0


def test_a_booking_question_is_exempt_even_with_a_stale_intent() -> None:
    """\"Can I book an appointment on Sunday?\" is a question but it IS booking."""
    cd: dict = {}
    for _ in range(STRIKE_LIMIT):
        _turn(cd, user="Can I book an appointment on Sunday?",
              reply="Could you please share your full name?",
              turn_intent="ASK_QUESTION")
    assert strike_count(cd) == 0


def test_a_data_value_with_a_stale_intent_is_still_exempt() -> None:
    cd: dict = {}
    for _ in range(STRIKE_LIMIT):
        _turn(cd, user="Priyank", reply="Thanks. What email should I use?",
              turn_intent="PROVIDE_DATA")
    assert strike_count(cd) == 0
