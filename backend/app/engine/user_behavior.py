"""User Behavior Understanding for WhatsApp — analyse every word before acting.

Covers: intent, sentiment, clarity, frustration, confusion, urgency,
engagement, and whether to start booking vs answer knowledge.

No hardcoding of business content — pure linguistic/behavioural signals.
Used by both whatsapp_cloud and wa-gateway (Baileys) before booking.
"""

import re
from typing import Dict, Any, List, Tuple

# --- Intent signals ---
_BOOKING_STRONG = (
    r"\b(?:book|booking|appointment|slot|schedule|reserve|meeting)\b.*\b(?:want|need|like|can|could|would|please|for|me|us|tomorrow|today|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday|am|pm|date|time)\b",
    r"\b(?:want|need|i'd like|i want|need to|let's|lets)\b.{0,30}\b(?:book|appointment|slot)\b",
    r"\b(?:tomorrow|today|next \w+)\b.*\b(?:at|am|pm|\d)\b",
)
_BOOKING_WEAK = (
    r"\b(?:book|appointment)\b",
)

_KNOWLEDGE_STRONG = (
    r"\b(?:what|which|when|where|who|how|can you|do you|tell me|explain|about|services?|offer|pricing|price|cost|hours|timings?|policy|available|open)\b.*\?",
    r"\b(?:do you allow|what is|who is|tell me about|information about|know about)\b",
)
_KNOWLEDGE_WEAK = (r"\b(?:dotstark|services?|hours?|timings?|policy)\b",)


_DECLINE_STRONG = (
    r"\bjust\s+kidding\b", r"\bwas\s+just\s+kidding\b", r"\bdon'?t\s+want\s+to\s+book\b",
    r"\bnot\s+interested\b", r"\bchanged\s+my\s+mind\b", r"\bno\s+booking\b",
)
_FRUSTRATION_STRONG = (
    r"\b(?:didn'?t\s+receiv|did\s+not\s+receiv|not\s+receiv(?:ing|ed)|still\s+not|never\s+receiv|haven'?t\s+receiv|hasn'?t\s+receiv|not\s+getting|where.*otp|no\s+otp|not\s+receiv)\b",
    r"\b(?:frustrated|annoying|confused|not\s+working|issue|problem)\b",
    r"[!?]{2,}", r"\b(?:codeeee|hellooo)\b",
)
_CONFUSION_STRONG = (
    r"\b(?:what\s+happening|why.*not|confused|what\s+is\s+issue|what's\s+going)\b",
    r"^(?:\?\?+|h\?+)$",
)

def _match_any(text: str, patterns: Tuple[str, ...]) -> bool:
    low = text.lower()
    return any(re.search(p, low, re.IGNORECASE) for p in patterns)

def _score(text: str, strong: Tuple[str, ...], weak: Tuple[str, ...]) -> float:
    if _match_any(text, strong):
        return 0.9
    if _match_any(text, weak):
        return 0.6
    return 0.0

def analyze_user_behavior(
    message_text: str,
    *,
    history: List[str] | None = None,
    collected_data: Dict[str, Any] | None = None,
    missing_fields: List[str] | None = None,
) -> Dict[str, Any]:
    """Return behaviour profile for one turn.

    Keys:
      primary_intent: book|knowledge|decline|frustrated|confused|unclear
      intent_scores: dict
      sentiment: positive|neutral|negative|frustrated
      clarity: clear|ambiguous|gibberish
      frustration_level: 0-1
      confusion_level: 0-1
      should_start_booking: bool
      should_answer_knowledge: bool
      needs_empathy: bool
      is_repeat: bool
    """
    text = (message_text or "").strip()
    low = text.lower()
    history = history or []
    collected_data = collected_data or {}
    missing_fields = missing_fields or []

    # Repeat detection
    is_repeat = False
    if history:
        last = (history[-1] or "").strip().lower()
        if last and low == last:
            is_repeat = True
        elif len(history) >= 2 and low in [h.lower().strip() for h in history[-3:]]:
            is_repeat = True

    # Scores
    book_s = _score(text, _BOOKING_STRONG, _BOOKING_WEAK)
    know_s = _score(text, _KNOWLEDGE_STRONG, _KNOWLEDGE_WEAK)
    decl_s = _score(text, _DECLINE_STRONG, ())
    frust_s = _score(text, _FRUSTRATION_STRONG, ())
    conf_s = _score(text, _CONFUSION_STRONG, ())

    # Adjust for history: repeated "Resend otp" is frustrated, not just book
    if is_repeat and "otp" in low:
        frust_s = max(frust_s, 0.85)
    # Heuristic: "didn't received any otp" etc.
    if ("didn't" in low or "didnt" in low or "did not" in low) and ("otp" in low or "code" in low or "receiv" in low or "got" in low):
        frust_s = max(frust_s, 0.9)

    # Sentiment
    sentiment = "neutral"
    if decl_s > 0.7 or frust_s > 0.7:
        sentiment = "frustrated" if frust_s > 0.7 else "negative"

    # Clarity
    clarity = "clear"
    if not text or len(text.split()) == 1 and len(text) < 4 and not text.isdigit():
        # single char like "a", "?" is ambiguous
        if re.match(r"^[^\w\s]+$", text):
            clarity = "gibberish"
    elif len(text) < 5 and not any(c.isalnum() for c in text):
        clarity = "gibberish"
    elif text.count("?") > 2 or "what" in low and len(text.split()) < 4:
        clarity = "ambiguous"

    # Primary intent — priority: decline > frustrated > confused > knowledge > book
    scores = {"book": book_s, "knowledge": know_s, "decline": decl_s, "frustrated": frust_s, "confused": conf_s}
    # Boost decline/frustrated to avoid "don't want to book any project" being seen as book
    if decl_s >= 0.85:
        primary = "decline"
    elif frust_s >= 0.85:
        primary = "frustrated"
    elif conf_s >= 0.85:
        primary = "confused"
    else:
        primary = max(scores, key=lambda k: scores[k])
    if scores[primary] < 0.5:
        primary = "unclear"
    # Final override: explicit decline phrases always win over book
    if decl_s >= 0.9 and book_s >= 0.9:
        primary = "decline"

    # Behavioural decisions
    should_start_booking = False
    should_answer_knowledge = False
    needs_empathy = False

    if primary == "decline":
        needs_empathy = True
        # Never start booking on decline
    elif primary == "frustrated":
        needs_empathy = True
        # Frustrated + OTP not received → resend, not new booking
        if "otp" in low or "code" in low:
            should_start_booking = False
            should_answer_knowledge = False
        elif know_s > 0.5:
            should_answer_knowledge = True
    elif primary == "confused":
        needs_empathy = True
        should_answer_knowledge = True
    elif primary == "knowledge":
        should_answer_knowledge = True
    elif primary == "book":
        # Only start booking if we have clear booking signal and not just a date fragment out of context
        # If user says "2026-09-29" alone with no booking verb, it's a date value, not a new intent — let field flow handle
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text.strip()) and missing_fields and missing_fields[0] == "date":
            should_start_booking = False  # let the date be consumed as field value
        else:
            should_start_booking = True
    elif primary == "unclear":
        # Ambiguous short text like "Okayy", "Hii" — don't start booking, just ack
        needs_empathy = False

    # If already in booking flow (missing_fields not empty and collected_data has some), don't auto-start a new booking.
    # The booking gate is elsewhere; this just informs the prompt.

    return {
        "primary_intent": primary,
        "intent_scores": scores,
        "sentiment": sentiment,
        "clarity": clarity,
        "frustration_level": frust_s,
        "confusion_level": conf_s,
        "is_repeat": is_repeat,
        "should_start_booking": should_start_booking,
        "should_answer_knowledge": should_answer_knowledge,
        "needs_empathy": needs_empathy,
        "text": text,
    }

def behaviour_to_prompt_hint(behaviour: Dict[str, Any]) -> str:
    """One-line hint for system prompt context block."""
    primary = behaviour.get("primary_intent", "unclear")
    sentiment = behaviour.get("sentiment", "neutral")
    if primary == "frustrated":
        return "USER_BEHAVIOUR: user is frustrated (repeated OTP not received). Be empathetic, acknowledge quickly, resend OTP if needed, offer HUMAN."
    if primary == "confused":
        return "USER_BEHAVIOUR: user is confused. Explain simply, one step at a time, no jargon."
    if primary == "decline":
        return "USER_BEHAVIOUR: user declines booking/joking. Respond formally: it's okay, they can reach out future. Do not list services."
    if primary == "knowledge":
        return "USER_BEHAVIOUR: user asks knowledge question. Answer from KB, keep booking on hold."
    if behaviour.get("should_start_booking"):
        return "USER_BEHAVIOUR: user wants to book. Proceed with booking flow ONE field at a time."
    return f"USER_BEHAVIOUR: {primary} ({sentiment}), clarity={behaviour.get('clarity')}. Respond helpfully, one idea."
