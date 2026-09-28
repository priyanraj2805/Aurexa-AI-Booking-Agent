"""Off-topic cooldown guardrail.

A customer can otherwise ask the assistant things it has nothing to do with,
forever, and burn an LLM call each time. After a run of off-topic questions
the session is briefly paused so the cost and the noise stop.

Rules:
  * N consecutive off-topic questions start a 15 second cooldown
  * while cooling down, ANY inbound message is answered with one short
    "you're still in cooldown" line and nothing else is processed
  * a single on-topic turn (booking or a knowledge answer) clears the counter,
    so a customer who is actually booking is never punished
  * the counter is stored on the session, so it survives a worker restart
"""

import re
import time

# Internal storage keys live under collected_data so they ride along with the
# existing session persistence and need no schema change.
_COUNT_KEY = "_offtopic_strike_count"
_UNTIL_KEY = "_offtopic_cooldown_until"

# How many off-topic questions in a row trigger the pause. Three is enough to
# distinguish genuine confusion from one stray question, and low enough that a
# real customer is not left waiting through repeated failures.
STRIKE_LIMIT = 3

COOLDOWN_SECONDS = 15 * 60  # 15 minutes

# Reply in whole minutes rather than counting down 900 seconds, which reads
# like a system error. Reported as "14 minutes", then "13 minutes", etc.
_SECONDS_PER_MINUTE = 60

# Seconds are stored as wall-clock floats. A message arriving after the window
# is simply allowed through, so there is no background job to run.
_MAX_CLOCK_SKEW = 1.0


def _now() -> float:
    return time.time()


def _get(container: dict, key: str, default=0):
    try:
        value = (container or {}).get(key, default)
    except AttributeError:
        return default
    return default if value is None else value


def cooldown_remaining(data: dict) -> int:
    """Whole seconds left on the cooldown, 0 when not cooling down.

    Returns 0 for an expired or malformed value rather than raising, because
    this runs on the inbound path where an exception would strand the
    customer mid-booking.
    """
    try:
        until = float(_get(data, _UNTIL_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0
    remaining = until - _now() - _MAX_CLOCK_SKEW
    if remaining <= 0:
        return 0
    return int(remaining) + 1


def is_cooling_down(data: dict) -> bool:
    return cooldown_remaining(data) > 0


def strike_count(data: dict) -> int:
    try:
        return int(float(_get(data, _COUNT_KEY, 0) or 0))
    except (TypeError, ValueError):
        return 0


def record_offtopic(data: dict) -> int:
    """Count one more off-topic question. Returns the new strike count.

    On reaching the limit the cooldown window is opened. Expired cooldowns are
    cleared here rather than elsewhere, so the state cannot get stuck.
    """
    if data is None:
        return 0
    if not is_cooling_down(data):
        data.pop(_UNTIL_KEY, None)

    count = strike_count(data) + 1
    data[_COUNT_KEY] = count

    if count >= STRIKE_LIMIT:
        data[_UNTIL_KEY] = _now() + COOLDOWN_SECONDS
    return count


def clear(data: dict) -> None:
    """Reset on any on-topic turn: a real booking step or a real answer."""
    if not data:
        return
    data.pop(_COUNT_KEY, None)
    data.pop(_UNTIL_KEY, None)


_PHRASE_RE = re.compile(
    r"\b(?:"
    r"how\s+much\s+does\s+(?:a|the|your)\b"
    r"|price|pricing|cost|costs|charges?|fees?"
    r"|discount|offer|offers|packages?|plans?"
    r"|do\s+you\s+have|do\s+you\s+offer|what\s+services"
    r"|tell\s+me\s+about|describe|explain"
    # "what is/are <a company thing>" only. Deliberately narrow: a bare
    # "what is" also matches trivia ("what is 2 + 2", "what is the capital
    # of France") and treating those as company questions would mean an
    # off-topic user is never counted.
    r"|what\s+(?:is|are|was|were)\s+(?:your|the\s+company|dotstark)\b"
    r"|what\s+does\s+(?:your|the\s+company|dotstark)\b"
    r"|details?\s+about|information\s+(?:about|on|regarding)"
    r"|when\s+are\s+you\s+open|opening\s+hours|business\s+hours"
    r"|where\s+are\s+you\s+located|your\s+(?:address|location)"
    r"|can\s+i\s+(?:speak|talk)\s+to"
    r"|(?:any|some)\s+(?:good|nice|fast|affordable)\s+"
    r")",
    flags=re.IGNORECASE,
)


def looks_like_asking_about_us(message_text: str) -> bool:
    """True when the customer may be asking a genuine company question.

    Used to keep a real question from counting as an off-topic strike. It is
    deliberately generous: a false positive only means one strike is not
    recorded, which costs nothing compared with blocking a paying customer.
    """
    text = str(message_text or "").strip()
    if not text:
        return False
    return bool(_PHRASE_RE.search(text))


# Replies that decline without ever containing one of the openers above. A
# live test showed the bot produces at least four different shapes of refusal
# for the same off-topic question:
#
#   "I'm a booking assistant, so I can only help with appointments..."
#   "I didn't quite catch that one, but no problem — we can keep going."
#   "I can help you book an appointment though. Could you please share..."
#   "Sorry, I don't have information about that."
#
# Matching only the first meant two of three off-topic questions recorded no
# strike at all and no cooldown ever opened. Judged on content instead: a
# reply that names what the assistant CAN do (book / check / reschedule /
# cancel / share your name for a booking) and contains no company answer is a
# decline, whatever it is worded like.
_DECLINE_SIDE_RE = re.compile(
    r"\b(?:book|booking|booked|appointment|appointments|reschedule|"
    r"cancel|share your (?:name|email|phone|number)|"
    r"i can help|i could help|i can only help|i'm here to help|"
    r"i am here to help|i didn't quite catch|didn't catch)\b",
    flags=re.IGNORECASE,
)

# Markers that show the reply is ANSWERING, not booking. Ordered separately
# because "would you like to book" trails almost every good answer, and
# judging the booking wording first threw out a correct Kentico answer.
_ANSWER_VERBS = re.compile(
    r"\b(?:we|i)\s+(?:offer|provide|build|develop|help|specialise|specialize|"
    r"work|deliver|have|also)\b|"
    r"\byes,?\s+(?:we|i)\b|"
    r"\bour\s+(?:team|services|developers|clients|experience|pricing|process|"
    r"approach|tech|stack|platforms?)\b|"
    r"\bwe\s+are\s+(?:a|an)\b|"
    r"\bwe\s+use\b|"
    r"\bwe\s+can\s+build\b",
    flags=re.IGNORECASE,
)

# A reply carrying any of these is answering something, so it is never a
# decline, whatever else it says.
_ANSWER_MARKERS = (
    "we offer", "we provide", "we build", "we develop", "we help",
    "our team", "our services", "our developers", "our clients",
    "we specialise", "we specialize", "we work with",
    "we deliver", "we have", "we also", "our experience",
    "our pricing", "our process", "our approach",
    "our tech stack", "our platforms", "our team",
)

# A reply that only moves the conversation ONTO booking is not a refusal.
# "I can help you book an appointment. Please share your name" is the normal
# next step of a booking, and counting it as an off-topic question struck
# genuine customers mid-booking.
#
# The booking prompt must be the WHOLE reply, not merely present in it. A
# decline often appends a booking prompt too ("To get back to booking, could
# you share your name? I'm a booking assistant, so I can only help with
# appointments"), and that shape was being read as progress rather than a
# refusal, so no strike was recorded and the cooldown never opened.
_BOOKING_PROMPT_ONLY_RE = re.compile(
    # A courtesy or transition opener, then either a "please share / could you
    # provide" ask, or a "what date would you like" question. Kept as two
    # explicit alternatives: a loose single pattern matched bare booking
    # questions and swallowed real booking progress.
    r"^(?:"
    # A bare "What time would you like to book?" with no opener at all.
    r"(?:what|which)\s+(?:date|time|day|slot)\b[^.!?]{0,60}"
    r"|"
    r"(?:sure|okay|ok|great|perfect|got it|thanks|thank you|hi|hello|alright)[,!.\s]*"
    r"(?:[^.!?]{0,40})?"
    r"(?:"
    r"(?:please|could you|can you|would you)\s*(?:please\s*)?"
    r"(?:share|provide|confirm|reply|send|enter|give|tell|let me know)\b"
    r"|what\s+(?:is|are)\s+your\s+(?:full\s+)?name\b"
    r"|(?:what|which)\s+(?:date|time|day|slot)\b"
    r")[^.!?]{0,60}"
    r"|"
    r"(?:i\s+can|i\s+could|i'?m\s+here\s+to\s+help|happy\s+to\s+help|"
    r"we\s+can\s+help|to\s+continue|to\s+get\s+back\s+to\s+booking|let'?s)"
    r"[^.!?]{0,40}"
    r"(?:"
    r"(?:please|could you|can you|would you)\s*(?:please\s*)?"
    r"(?:share|provide|confirm|reply|send|enter|give|tell|let me know)\b"
    r"|what\s+(?:is|are)\s+your\s+(?:full\s+)?name\b"
    r")[^.!?]{0,60}"
    r"|"
    r"(?:what|which)\s+[\w\s]{0,30}?(?:would\s+you\s+like|do\s+you\s+prefer|"
    r"suits?\s+you|works?\s+for\s+you)\b[^.!?]{0,40}"
    r"(?:what|which)\s+(?:is|are)?\s*[\w\s]{0,30}?"
    r"(?:would\s+you\s+like|do\s+you\s+prefer|suits?\s+you|works?\s+for\s+you)\b"
    r"[^.!?]{0,40}"
    r")"
    r"[^.!?]{0,40}[.!?]?\s*$",
    flags=re.IGNORECASE,
)


# The reply admits it could not understand the customer. This is the single
# most reliable decline signal, and it covers the shapes the other patterns
# miss ("I didn't quite catch that one, but no problem"). Checked before the
# booking-prompt test, because a decline often ends by asking for a name.
_DID_NOT_CATCH_RE = re.compile(
    r"\b(?:i\s+)?(?:did\s*n[o']?t|did\s+not|could\s*n[o']?t|could\s+not|"
    r"was\s+unable\s+to|failed\s+to)\s+"
    r"(?:quite\s+)?(?:catch|understand|make\s+out|get|work)"
    r"|\bnot\s+sure\s+(?:what\s+you|how\s+to)\b"
    r"|\bi\s+don'?t\s+understand\b",
    flags=re.IGNORECASE,
)


def looks_like_decline(reply: str) -> bool:
    """True when a reply turns the customer away rather than answering.

    Used to count off-topic questions. Judged on what the reply DOES rather
    than on an exact phrase, because the model words the same refusal several
    different ways and a phrase list silently missed most of them.
    """
    text = str(reply or "").strip()
    if not text:
        return False
    lowered = text.lower()
    # Order matters, and it is the order that makes each check correct:
    #   1. an explicit "I didn't catch that" is a refusal, full stop
    #   2. an ANSWER is not a refusal, even when it trails "would you like to
    #      book?" — a correct Kentico answer was being discarded
    #   3. a reply that is ONLY a booking next-step is normal progress
    #   4. anything else naming booking help without answering is a refusal
    if _DID_NOT_CATCH_RE.search(text):
        return True
    if _ANSWER_VERBS.search(text) or any(m in lowered for m in _ANSWER_MARKERS):
        return False
    if _BOOKING_PROMPT_ONLY_RE.match(text):
        return False
    if "booking assistant" in lowered:
        return True
    try:
        from app.engine.knowledge_guards import is_knowledge_fallback_text

        if is_knowledge_fallback_text(text):
            return True
    except Exception:
        pass
    return bool(_DECLINE_SIDE_RE.search(text))


def cooldown_message(remaining: int) -> str:
    """The reply sent for any message DURING the cooldown.

    Deliberately different from the opening line: "still" tells the customer
    this is ongoing, not new. The wait is reported in whole minutes, because
    a raw seconds count on a 15 minute pause reads like a malfunction rather
    than a deliberate wait.
    """
    seconds = max(1, int(remaining or 0))
    minutes = (seconds + _SECONDS_PER_MINUTE - 1) // _SECONDS_PER_MINUTE
    if minutes <= 1:
        return "You're still in cooldown. Please try again in about a minute."
    return (
        f"You're still in cooldown. Please try again in about {minutes} minutes."
    )


def cooldown_opening_message() -> str:
    """The reply sent ONCE, on the turn that opens the cooldown.

    Deliberately has no "still": this is the first the customer hears of it.
    Kept short and plain so it reads as a notice, not a punishment.
    """
    minutes = max(1, int(COOLDOWN_SECONDS // _SECONDS_PER_MINUTE))
    unit = "minute" if minutes == 1 else "minutes"
    return f"You are in cooldown. You can continue after {minutes} {unit}."
