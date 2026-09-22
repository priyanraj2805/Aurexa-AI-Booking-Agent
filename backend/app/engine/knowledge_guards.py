"""Knowledge grounding guards against hallucination (all channels).

Three deterministic layers — no LLM judgment involved:

1. gate_chunks_by_relevance: drop retrieved chunks that share no topical
   overlap with the query (unless LLM-rerank scored them relevant). Stops
   false-confidence grounding ("looks sourced, isn't").
2. verify_knowledge_answer: the composed answer must overlap the grounded
   material, or be an explicit honest decline. Otherwise it is replaced.
3. find_unlisted_entities: phone numbers, prices, emails and clock-times in
   the final text must appear in the allowed material (KB chunks + org
   facts + the customer's own session details). Anything else is treated
   as invented contact/pricing info and blocked.

No hardcoded business content anywhere — every check runs on live data.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset(
    "what when where which who whom whose why how much many long does is are was were "
    "be been being have has had will would should could can may might shall must "
    "the a an and or but if then else for with from about into over after before "
    "this that these those there here your our their its his her my me you we they "
    "them him her us do not no yes please tell give show know book booking appointment "
    "also just like get got need want would like".split()
)

_DECLINE_PHRASES = (
    "don't have",
    "do not have",
    "no information",
    "not available",
    "don't know",
    "can't help with that",
)

_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PRICE_RE = re.compile(r"(?:₹|Rs\.?|INR|\$)\s?\d[\d,]*")
_CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\s?(?:AM|PM|am|pm)?\b|\b\d{1,2}\s?(?:AM|PM|am|pm)\b")


def _stem(word: str) -> str:
    """Light stem so open/opens, hour/hours, consultation/consultations match."""
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def content_words(text: str) -> Set[str]:
    """Topical tokens: alphabetic, len>=4, stemmed, minus stopwords."""
    words = set(re.findall(r"[^\W\d_]{4,}", (text or "").lower()))
    return {_stem(w) for w in words if w not in _STOPWORDS}


def _adaptive_need(n_words: int, default: int = 2) -> int:
    """Short queries/answers can't share many words — scale the bar."""
    if n_words <= 2:
        return 1
    return default


def gate_chunks_by_relevance(
    chunks: List[Dict[str, Any]],
    query: str,
    *,
    min_overlap: int = 2,
    min_rerank_score: int = 4,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Split retrieved chunks into (kept, dropped_all).

    A chunk survives when the LLM rerank scored it relevant OR it shares
    topical overlap with the query. Returns kept list + whether a non-empty
    input was gated down to nothing.
    """
    chunks = list(chunks or [])
    if not chunks:
        return [], False
    query_words = content_words(query)
    need = _adaptive_need(len(query_words), min_overlap)
    kept: List[Dict[str, Any]] = []
    for chunk in chunks:
        try:
            score = int(chunk.get("rerank_score") or 0)
        except (ValueError, TypeError):
            score = 0
        if score >= min_rerank_score:
            kept.append(chunk)
            continue
        haystack = f"{chunk.get('content') or ''} {chunk.get('snippet') or ''}"
        if len(content_words(haystack) & query_words) >= need:
            kept.append(chunk)
    return kept, not kept


def is_honest_decline(answer: str) -> bool:
    lowered = (answer or "").lower()
    return any(phrase in lowered for phrase in _DECLINE_PHRASES)


def verify_knowledge_answer(
    answer: str, chunks: List[Dict[str, Any]], *, min_overlap: int = 2
) -> Tuple[bool, str]:
    """Check a composed knowledge answer against its grounding.

    Returns (ok, reason): "decline" | "grounded" | "ungrounded".
    Honest declines always pass; anything else must share topical overlap
    with the chunk material it claims to come from.
    """
    if is_honest_decline(answer):
        return True, "decline"
    pooled = " ".join(
        f"{c.get('content') or ''} {c.get('snippet') or ''}" for c in (chunks or [])
    )
    answer_words = content_words(answer)
    if len(answer_words & content_words(pooled)) >= _adaptive_need(
        len(answer_words), min_overlap
    ):
        return True, "grounded"
    return False, "ungrounded"


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _digit_groups(text: str) -> Set[str]:
    """Contiguous digit runs (spaces/dashes inside one number kept together).

    Exact-group comparison avoids substring traps (a lower fee matching
    inside a higher one) while tolerating formatting differences
    (country codes, spaces, dashes).
    """
    groups = set()
    for run in re.findall(r"\d[\d\s\-()]*\d|\d", text or ""):
        digits = _digits_only(run)
        if len(digits) >= 2:
            groups.add(digits)
    return groups


def find_unlisted_entities(
    answer: str,
    allowed_texts: List[str],
    *,
    kinds: Tuple[str, ...] = ("phone", "email", "price"),
) -> List[str]:
    """Entities in `answer` absent from every allowed text.

    Enforced kinds default to contact/money (phone, email, price) — the
    dangerous inventions. Clock-times and dates are NOT enforced: booking
    confirmations legitimately echo slot times/dates that never appear in
    the knowledge base.

    Compares digit-normalized (phones/prices survive formatting
    differences); emails case-insensitively. Returns the offending
    substrings (deduplicated, capped).
    """
    pooled = " ".join(t or "" for t in allowed_texts)
    pooled_groups = _digit_groups(pooled)
    pooled_lower = pooled.lower()
    found: List[str] = []

    def _missing(candidate: str, *, digit_compare: bool) -> bool:
        if digit_compare:
            return _digits_only(candidate) not in pooled_groups
        return candidate.lower() not in pooled_lower

    _patterns = {
        "phone": (_PHONE_RE, True),
        "price": (_PRICE_RE, True),
        "email": (_EMAIL_RE, False),
        "clock": (_CLOCK_RE, True),
    }
    for kind in kinds:
        entry = _patterns.get(kind)
        if entry is None:
            continue
        pattern, digit_compare = entry
        for match in pattern.finditer(answer or ""):
            candidate = match.group(0).strip()
            if _missing(candidate, digit_compare=digit_compare):
                if candidate not in found:
                    found.append(candidate)
                if len(found) >= 5:
                    return found
    return found


def knowledge_decline(step_hint: str = "") -> str:
    """Honest decline with an optional booking-step re-anchor.

    `step_hint` is caller-supplied live context (e.g. the pending step
    title) — never invented here.
    """
    base = (
        "Sorry, I don't have that information in our records right now. "
        "If you'd like to continue with your booking, just reply and I'll "
        "pick up where we left off."
    )
    if step_hint:
        base = (
            "Sorry, I don't have that information in our records right now. "
            f"{step_hint}"
        )
    return base


_PAYMENT_TXN_WORDS = ("pay", "payment", "razorpay", "order", "upi", "transaction")


def guard_final_text(
    body: str,
    *,
    org_facts: List[str],
    session_details: List[str],
) -> Tuple[str, bool, List[str]]:
    """Final gate for an outgoing reply: block invented contact/money data.

    Fast path: no phone/email/price entities → pass-through untouched.
    Otherwise every entity must appear in the allowed material (org facts
    like business profile, plus the customer's own session details echoed
    back in confirmations). Payment-transaction texts skip price
    enforcement (amounts come from live payment attempts, not the KB).
    Returns (safe_body, blocked, entities).
    """
    kinds: Tuple[str, ...] = ("phone", "email", "price")
    lowered = (body or "").lower()
    if any(word in lowered for word in _PAYMENT_TXN_WORDS):
        kinds = ("phone", "email")
    entities = find_unlisted_entities(
        body, [*(org_facts or []), *(session_details or [])], kinds=kinds
    )
    if not entities:
        return body, False, []
    logger.warning("knowledge entity gate blocked unlisted %s", entities)
    return knowledge_decline(), True, entities
