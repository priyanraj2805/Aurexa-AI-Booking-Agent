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

# Phone matcher. The {7,16} span tolerates real intra-number formatting
# (a country code plus two space-separated groups) while staying too short to
# span two separate numbers written side by side. Unbounded, it fused
# neighbours into one bogus "phone" and produced both false blocks and missed
# inventions.
_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{7,16}\d")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PRICE_RE = re.compile(r"(?:₹|Rs\.?|INR|\$)\s?\d[\d,]*")
_CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\s?(?:AM|PM|am|pm)?\b|\b\d{1,2}\s?(?:AM|PM|am|pm)\b")

# Two-or-more capitalized words: person-name shaped ("Aditya Grover").
# Single capitalized words (Monday, DotStark, Confirm) are too noisy.
_NAME_CANDIDATE_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+)\b")

# Boilerplate / structural phrases that match the name shape but are never
# a person's name. Generic site-chrome English — never business content.
_GENERIC_NAME_PHRASES = frozenset(
    {
        "about us", "contact us", "privacy policy", "terms conditions",
        "terms of service", "all rights reserved", "customer support",
        "support team", "help center", "knowledge base", "frequently asked",
        "asked questions", "read more", "learn more", "get started",
        "book now", "book appointment", "privacy statement",
    }
)

# Job-title words: "Chief Executive Officer" matches the shape but names
# nobody. A candidate made ONLY of these is skipped.
_TITLE_WORDS = frozenset(
    {
        "chief", "executive", "officer", "founder", "co-founder", "cofounder",
        "president", "vice", "director", "managing", "manager", "head",
        "chairman", "chairwoman", "partner", "associate", "assistant",
        "senior", "junior", "lead", "team",
    }
)

# Conversational openers and instruction verbs that glue onto a capitalized
# word ("Thanks Somya", "See About Us", "Confirm Monday"). Generic UX
# English — never business content.
_NON_NAME_LEADERS = frozenset(
    {
        "thanks", "thank", "hello", "hi", "hey", "dear", "welcome",
        "sure", "great", "perfect", "sorry", "yes", "no", "okay",
        "see", "check", "visit", "read", "find", "learn", "explore",
        "discover", "click", "open", "confirm", "select", "choose",
        "pick", "share", "provide", "enter", "type", "send", "reply",
    }
)


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


# RFC 2606 reserved documentation domains — can never be a real address.
# Bots use them as illustrative examples ("like name@example.com"); masking
# one as [hidden] destroys the example while protecting nothing.
_PLACEHOLDER_EMAIL_DOMAINS = frozenset(
    {"example.com", "example.org", "example.net"}
)


def _is_placeholder_entity(candidate: str, kind: str) -> bool:
    """True when a matched entity is demonstrably a placeholder, not data.

    Only shapes that cannot be real are exempt — a genuinely invented
    contact number/address must still be masked. Checked BEFORE the
    allow-list comparison so placeholders pass regardless of materials.
    """
    text = str(candidate or "").strip()
    if not text:
        return False
    if kind == "email":
        domain = text.rsplit("@", 1)[-1].strip().lower() if "@" in text else ""
        return domain in _PLACEHOLDER_EMAIL_DOMAINS
    if kind == "phone":
        digits = _digits_only(text)
        if len(digits) < 7:
            # Too short to be a real number — a dial fragment, not data.
            return True
        if digits and len(set(digits)) == 1:
            return True
        # 555-XXXX fictional range used in examples ("+1 (555) 000-0000").
        if "555" in digits:
            return True
        return False
    return False


def _digit_groups(text: str) -> Set[str]:
    """Digit groups for the ALLOWED side, tokenized exactly like candidates.

    Two passes, both required:

    1. Formatted numbers, using the same bounded pattern as _PHONE_RE, so
       "+<cc> <a> <b>" on the allowed side produces the identical digit string
       the answer side produces for the same number. Symmetry is what makes
       the comparison reliable — when the two sides split differently, a
       legitimate number could never line up with its own stored form and was
       reported as invented.
    2. Plain digit runs of two or more digits, so short values (prices like
       500, clock/date fragments) are present. Without this, short numbers
       were absent from the allowed set and every price mention was flagged.

    The allowed set is a whitelist, so erring towards MORE groups is safe:
    it can only cause a missed invention, never a false block of a valid
    reply. False blocks are the dangerous direction (they replaced working
    OTP prompts with an apology), so generosity is deliberate here.

    IMPORTANT: pass ONE logical value per call. Concatenating several values
    into a single string lets a greedy formatted run swallow the next value,
    so callers build the group set by iterating values (see
    find_unlisted_entities) rather than joining them.
    """
    groups = set()
    if not text:
        return groups
    # Formatted numbers, bounded exactly like the answer-side matcher.
    for run in re.findall(_PHONE_RE.pattern, text):
        digits = _digits_only(run)
        if len(digits) >= 2:
            groups.add(digits)
    # Plain runs: prices, times, dates, and bare numbers.
    for run in re.findall(r"\d{2,}", text):
        groups.add(run)
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

    Each allowed text is scanned SEPARATELY and the digit groups unioned.
    Joining them into one pooled string first (the old behaviour) let a
    single greedy digit run merge neighbouring values, so a legitimate
    customer phone number stored as "+916350063338" next to their date
    "2026-09-28" collapsed into "916350063338202609283", stopped being
    recognised as the customer's own number, and was reported as invented —
    which silently replaced valid OTP replies with a knowledge decline.
    """
    # Union of per-value digit groups. Never pool into one string.
    pooled_groups: Set[str] = set()
    for text_value in allowed_texts or []:
        if text_value:
            pooled_groups |= _digit_groups(str(text_value))
    pooled_lower = " ".join(str(t or "") for t in (allowed_texts or [])).lower()
    found: List[str] = []

    def _looks_like_date(candidate: str) -> bool:
        c = candidate.strip()
        # ISO date or time like 2026-09-25 or 09:00 AM - these are booking slots, not phone
        if re.match(r"^\d{4}-\d{2}-\d{2}$", c):
            return True
        if re.match(r"^\d{1,2}:\d{2}\s*(AM|PM|am|pm)?$", c.strip()):
            return True
        if re.match(r"^\d{1,2}-\d{2}-\d{4}$", c):
            return True
        return False

    def _missing(candidate: str, *, digit_compare: bool) -> bool:
        if _looks_like_date(candidate):
            return False
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
            if _is_placeholder_entity(candidate, kind):
                continue
            if _missing(candidate, digit_compare=digit_compare):
                if candidate not in found:
                    found.append(candidate)
                if len(found) >= 5:
                    return found
    return found


def find_ungrounded_names(
    answer: str,
    allowed_texts: List[str],
    *,
    safe_names: Tuple[str, ...] = (),
) -> List[str]:
    """Person-name-shaped phrases in `answer` absent from every allowed text.

    Catches training-knowledge invention ("Aditya Grover is on the
    leadership team") when the retrieved materials name nobody (or name
    others). A candidate is skipped when it is site boilerplate, a bare job
    title, a conversational opener glued to a name ("Thanks Somya"), or an
    explicitly safe name (business / agent / persona names from live config).
    No business content is hardcoded — every comparison runs on live data.
    Returns the offending phrases (deduplicated, capped).
    """
    if is_honest_decline(answer):
        return []
    pooled_lower = " ".join(t or "" for t in allowed_texts).lower()
    safe_lower = {str(n or "").strip().lower() for n in safe_names if str(n or "").strip()}
    found: List[str] = []
    for match in _NAME_CANDIDATE_RE.finditer(answer or ""):
        candidate = match.group(1).strip()
        lowered = candidate.lower()
        if lowered in _GENERIC_NAME_PHRASES or lowered in safe_lower:
            continue
        words = lowered.split()
        if all(word in _TITLE_WORDS for word in words):
            continue
        if words and words[0] in _NON_NAME_LEADERS:
            continue
        if lowered in pooled_lower:
            continue
        if candidate not in found:
            found.append(candidate)
        if len(found) >= 5:
            break
    return found


_UNLISTED_PLACEHOLDER = "[hidden]"

# Detects the *class* of canned "I have no information for you" replies so they
# can be suppressed while a booking is waiting on the customer. Expressed as a
# pattern rather than a literal sentence: the canned apology itself has been
# removed from the product, and this only guards against a model reproducing
# the phrasing on its own.
_KNOWLEDGE_FALLBACK_RE = re.compile(
    r"(?:don'?t|do\s+not|cannot|can'?t)\s+have\s+(?:that|any|the)?\s*"
    r"(?:information|record|details?)"
    r"|no\s+(?:information|record|details?)\s+(?:in|on)\s+(?:our|the)\s+records?",
    re.IGNORECASE,
)


def is_knowledge_fallback_text(text: str) -> bool:
    """True when the text is a content-free "I don't have that" apology."""
    return bool(_KNOWLEDGE_FALLBACK_RE.search(text or ""))


def mask_unlisted_entities(body: str, entities: List[str]) -> str:
    """Neutralise invented contact/money details in place.

    Replaces only the offending substrings, so the rest of a useful answer
    survives. This replaces the previous behaviour of discarding the entire
    reply and replacing it with a canned apology, which regularly destroyed
    valid booking messages (an OTP prompt echoing the customer's own number
    was thrown away and the customer was told nothing was on record).
    """
    text = body or ""
    for entity in entities or []:
        token = str(entity or "").strip()
        if not token:
            continue
        if token in text:
            text = text.replace(token, _UNLISTED_PLACEHOLDER)
    return text


_PAYMENT_TXN_WORDS = ("pay", "payment", "razorpay", "order", "upi", "transaction")

# Replies the booking engine generates from live session state. They are not
# knowledge answers, so a knowledge-grounding verdict must never overwrite
# them: an OTP prompt that echoes the customer's own number is legitimate
# transactional copy, and replacing it with "I don't have that information"
# strands the customer mid-booking. Detected from the rendered text, not from
# a flag, so every channel benefits.
_TRANSACTIONAL_REPLY_MARKERS = (
    "verification code",
    "one-time code",
    "otp",
    "resend",
    "code expires",
    "enter the code",
    "enter it here",
    "we'll text",
    "we will text",
    "whatsapp you",
    "sent a new",
)


def is_transactional_reply(body: str) -> bool:
    """True when the reply is booking/OTP transactional copy, not knowledge.

    Such replies must pass through the knowledge entity gate untouched.
    """
    lowered = f"{(body or '').strip().lower()}"
    if not lowered:
        return False
    return any(marker in lowered for marker in _TRANSACTIONAL_REPLY_MARKERS)


def guard_final_text(
    body: str,
    *,
    org_facts: List[str],
    session_details: List[str],
) -> Tuple[str, bool, List[str]]:
    """Final gate for an outgoing reply: block invented contact/money data.

    Fast path: no phone/email/price entities → pass-through untouched.
    Transactional replies (OTP prompts, resends, code-entered notices) are
    exempt: their contact details come from the live booking session, not
    from the knowledge base.
    Otherwise every entity must appear in the allowed material (org facts
    like business profile, plus the customer's own session details echoed
    back in confirmations). Payment-transaction texts skip price
    enforcement (amounts come from live payment attempts, not the KB).
    Returns (safe_body, blocked, entities).
    """
    kinds: Tuple[str, ...] = ("phone", "email", "price")
    lowered = (body or "").lower()

    # Transactional copy (OTP prompt / resend / code-entered notice) is
    # generated from the live booking session, not from the knowledge base.
    # Grounding it here is meaningless and, when the entity scan misfires,
    # actively harmful: it replaced a valid OTP prompt with a knowledge
    # decline and stranded the customer mid-booking.
    if is_transactional_reply(body):
        return body, False, []

    if any(word in lowered for word in _PAYMENT_TXN_WORDS):
        kinds = ("phone", "email")
    entities = find_unlisted_entities(
        body, [*(org_facts or []), *(session_details or [])], kinds=kinds
    )
    if not entities:
        return body, False, []
    # Invented contact/money details are neutralised in place instead of the
    # whole reply being replaced by a canned apology. That message pipeline has
    # been removed: it answered nothing, and during a booking it stranded
    # customers by overwriting valid messages.
    logger.warning("knowledge entity gate masked unlisted %s", entities)
    return mask_unlisted_entities(body, entities), True, entities
