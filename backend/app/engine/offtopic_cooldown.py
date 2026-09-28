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


def cooldown_message(remaining: int) -> str:
    """The single reply sent while cooling down.

    States the wait plainly and does not process the message, so the customer
    is not left thinking their question was silently swallowed. The wait is
    reported in whole minutes, because a raw seconds count on a 15 minute
    pause reads like a malfunction rather than a deliberate wait.
    """
    seconds = max(1, int(remaining or 0))
    minutes = (seconds + _SECONDS_PER_MINUTE - 1) // _SECONDS_PER_MINUTE
    if minutes <= 1:
        return "You're still in cooldown. Please try again in about a minute."
    return (
        f"You're still in cooldown. Please try again in about {minutes} minutes."
    )
