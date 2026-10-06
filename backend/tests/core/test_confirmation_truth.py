"""A booking may only be CONFIRMED when a Booking row actually exists.

Observed production failure (09:03:39): the bot sent

  "Thanks Will. Your appointment is confirmed for October 5th at 3 PM.
   We look forward to seeing you."

ten seconds BEFORE the verification code was even sent, with no Booking
row, no calendar event, no email and no notification. The customer was
told they were booked when they were not.

The fix places one authoritative check at the last point before the reply
leaves the orchestrator, so it covers every path including early returns.
"""
import inspect

from app.engine.user_visible_text import looks_like_confirmation_claim

# The two sentences observed in production, plus close variants.
FALSE_CONFIRMATIONS = [
    "Thanks Will. Your appointment is confirmed for October 5th at 3 PM. "
    "We look forward to seeing you.",
    "Your appointment is confirmed for October 5th at 3 PM.",
    "Thanks Will. Your appointment is confirmed for October 5th.",
    "Your appointment is confirmed.",
    "Your booking is confirmed for October 5th at 3 PM.",
    "Great, your appointment is confirmed. We look forward to seeing you.",
    "Your appointment has been booked for tomorrow at 10 AM.",
    "All confirmed! See you on 5 October at 3 PM.",
    # Date/time subjects with a completion adverb (production 2026-10-05:
    # "That date is already confirmed for your appointment." reached a
    # customer with no Booking row).
    "That date is already confirmed for your appointment.",
    "I see you're looking at October 6, 2026. That date is already "
    "confirmed for your appointment.",
    "Your time is successfully booked.",
]


def test_date_subject_claims_need_an_adverb_of_completion() -> None:
    """Bare date/time phrasing must stay safe: questions, mid-flow text
    ("the date is set"), negations and confirm-requests are not claims."""
    for safe in [
        "The date is set for October 6, please pick a time.",
        "Is your date confirmed?",
        "Please confirm the booking.",
        "Your slot is not booked yet.",
        "Thanks. What date would you like to book?",
        "That date is confirmed?",  # question form, no adverb
    ]:
        assert looks_like_confirmation_claim(safe) is False, (
            f"a legitimate message would be wrongly blocked: {safe!r}"
        )


def test_every_false_confirmation_phrase_is_detected() -> None:
    """The wording guard must catch these, or nothing downstream can help."""
    for phrase in FALSE_CONFIRMATIONS:
        assert looks_like_confirmation_claim(phrase) is True, (
            f"a false confirmation would reach the customer undetected: {phrase!r}"
        )


def test_the_guard_runs_at_the_last_point_before_the_reply_leaves() -> None:
    """Placement is the fix. An earlier guard was bypassed on at least one
    path, so the check must sit immediately before the ActionPayload that is
    returned to the channel.

    Scoped to the WhatsApp orchestrator function that produces the reply, not
    the whole module: other functions build their own payloads and are not
    the path this bug travelled.
    """
    import app.engine.langgraph_orchestrator as orch

    # Read the file and take the LAST payload, which is the one the customer
    # receives. getmembers() is not usable here: several functions in this
    # module build their own ActionPayload, and picking the wrong one made
    # this test fail for the wrong reason.
    path = inspect.getsourcefile(orch)
    assert path is not None, "could not locate the orchestrator source"
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.readlines()

    # Anchor on the end-of-turn guard's call site, not the helper definition
    # or the earlier knowledge-path calls.
    guard_line = next(
        (
            i
            for i, ln in enumerate(lines)
            if "_guarded = await _guard_confirmation_claim(" in ln
        ),
        None,
    )
    assert guard_line is not None, "the final confirmation guard is missing"

    # The payload built after the guard, in the same function, is the one that
    # reaches the channel.
    payload_line = next(
        (
            i
            for i in range(guard_line, min(guard_line + 120, len(lines)))
            if "action = ActionPayload(" in lines[i]
        ),
        None,
    )
    assert payload_line is not None, (
        "no outbound ActionPayload found after the guard; the guard must sit "
        "immediately before the reply is returned"
    )
    assert guard_line < payload_line


def test_every_early_return_of_model_text_also_calls_the_guard() -> None:
    """The guard used to exist only here, while its own comment claimed it
    covered "every path, including the ones that return early". It did not:
    production sent "Your appointment is booked for October 2, 2026 at 3:00 PM"
    with zero Booking rows. Every path that returns a model-authored message
    must now call the guard itself.
    """
    import app.engine.langgraph_orchestrator as orch

    src = inspect.getsource(orch)
    # The two knowledge paths, plus the end-of-turn path.
    assert src.count("await _guard_confirmation_claim(") >= 3, (
        "expected the guard on both knowledge early-return paths and the "
        "end-of-turn path"
    )


def test_the_guard_requires_a_persisted_booking_not_just_a_flag() -> None:
    """A verification flag is not a booking. The check must consult the
    database for a Booking row, which is the only real evidence."""
    import app.engine.langgraph_orchestrator as orch

    src = inspect.getsource(orch._guard_confirmation_claim)
    assert "_session_has_persisted_booking" in src, (
        "the guard must verify a persisted Booking row, not trust an "
        "in-memory flag that a later turn can set without booking anything"
    )


def test_the_guard_restores_the_review_card() -> None:
    """Blocking the false claim is not enough on its own: the customer must be
    given the review card so they can still confirm and complete the booking."""
    import app.engine.langgraph_orchestrator as orch

    src = inspect.getsource(orch._guard_confirmation_claim)
    assert "_build_confirmation_summary" in src, (
        "a blocked confirmation must fall back to the summary, otherwise the "
        "customer is left with no way to confirm"
    )


def test_legitimate_post_booking_confirmations_are_not_blocked() -> None:
    """The real confirmation after OTP must still be allowed. These are
    recognised as claims; the guard permits them because a Booking row exists
    by then, so this test only pins the wording that must remain claim-shaped."""
    for phrase in [
        "🎉 Your booking is confirmed!",
        "Your booking is already confirmed for 30 Sep 2026 at 10:30 AM. "
        "Let us know if you need anything else.",
    ]:
        assert looks_like_confirmation_claim(phrase) is True


def test_a_review_summary_is_not_itself_a_confirmation_claim() -> None:
    """The review card must not trip the guard, or the customer could never
    reach the confirmation step."""
    import app.engine.message_processor as mp

    class _Session:
        collected_data = {
            "name": "Will",
            "email": "will@example.com",
            "phone": "+916350063338",
            "date": "2026-10-05",
            "time": "3:00 PM",
        }
        session_status = "ACTIVE"

    def _fd(icon, label, ftype):
        return type("F", (), {"icon": icon, "label": label, "type": ftype,
                              "unit": None, "options": None})()

    fd_map = {
        "name": _fd("\U0001f468", "Full Name", "text"),
        "email": _fd("\U0001f4e7", "Email Address", "email"),
        "phone": _fd("\U0001f4de", "Phone Number", "phone"),
        "date": _fd("\U0001f4c5", "Preferred Date", "date"),
        "time": _fd("\u23f0", "Preferred Time", "time"),
    }

    summary, directive = mp._build_confirmation_summary(_Session(), fd_map)

    assert directive == "REQUEST_CONFIRMATION"
    assert looks_like_confirmation_claim(summary) is False, (
        "the review card must not read as a confirmation, or the guard would "
        "block the very message the customer needs in order to confirm"
    )
    assert "3:00 PM" in summary
