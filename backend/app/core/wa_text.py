"""Central outbound text formatting for WhatsApp.

Every customer-facing reply passes through :func:`format_outbound` before it
is sent, so formatting is guaranteed consistent no matter which engine path,
template or fallback produced the text.

Why this exists: replies were composed in dozens of places (LangGraph
orchestrator, legacy message processor, cancel flow, decline flow, directive
acknowledgements) and each one invented its own layout. The result was cold,
run-on prose with no visual structure, markdown bold that WhatsApp rendered as
literal asterisks, and internal prompt/template headers such as
"BUSINESS HOURS" leaking to customers as the opening line of a reply.

WhatsApp formatting rules applied here:
  * bold is a SINGLE asterisk pair: *text*  ( **text** renders literally )
  * italic is a single underscore pair: _text_
  * strikethrough is a tilde pair: ~text~
  * monospace is three backticks: ```text```
  * paragraphs are separated by exactly one blank line
  * there is no heading syntax, so headings are expressed with bold + emoji
"""

import re
from typing import Optional

# Internal section headers that leaked from prompt/template scaffolding into
# customer replies. Matched only as a standalone ALL-CAPS line.
_LEAKED_HEADERS = (
    "BUSINESS HOURS",
    "OUR SERVICES",
    "SERVICES OFFERED",
    "FAQ",
    "FREQUENTLY ASKED QUESTIONS",
    "CONTACT US",
    "ABOUT US",
    "OPENING HOURS",
    "WORKING HOURS",
    "IMPORTANT INFORMATION",
    "INSTRUCTIONS",
    "SYSTEM PROMPT",
    "CONTEXT",
    "KNOWLEDGE BASE",
    "EXAMPLE",
    "EXAMPLES",
    "GUIDELINES",
    "RESPONSE FORMAT",
    "TONE",
    "PERSONA",
)

_ALL_CAPS_HEADER_RE = re.compile(r"^[A-Z][A-Z0-9 &'/,\-]{3,}$")
_BOLD_LINE_RE = re.compile(r"^\s*[*_~]{1,3}\s*$")


def strip_leaked_headers(text: str) -> str:
    """Remove prompt/template scaffolding that leaked into a reply.

    Only a leading ALL-CAPS line is treated as a leaked header, and only when
    it matches a known section name or looks like a bare section label. Real
    sentences keep their capitals, so normal replies are untouched.

    A section name may carry a trailing parenthetical note, e.g.
    "BUSINESS HOURS (authoritative, answer timing questions from this)". That
    annotation is aimed at the model, not the customer, and it used to defeat
    the ALL-CAPS check entirely -- the whole line was then sent to the
    customer as the opening line of the reply.
    """
    if not text:
        return text
    lines = text.split("\n")
    removed = 0
    while removed < 2 and lines and not lines[0].strip():
        lines.pop(0)
        removed += 1
    if not lines:
        return text
    first = lines[0].strip()
    if not first:
        return text
    upper = first.upper()
    if upper in _LEAKED_HEADERS or first.replace("'", "").upper() in _LEAKED_HEADERS:
        lines.pop(0)
        return "\n".join(lines)
    # Section name plus a model-facing annotation in parentheses.
    annotation = re.match(r"^([A-Z][A-Z0-9 &'/,\-]{2,})\s*\([^()]*\)\s*$", first)
    if annotation:
        # The character class is greedy and swallows the space before "(".
        name = annotation.group(1).strip().upper()
        if name in _LEAKED_HEADERS or name.replace("'", "") in _LEAKED_HEADERS:
            lines.pop(0)
            return "\n".join(lines)
    if not _ALL_CAPS_HEADER_RE.match(first):
        return text
    return "\n".join(lines)


def normalize_bold(text: str) -> str:
    """Convert markdown bold to WhatsApp bold and repair broken pairs.

    ``**text**`` -> ``*text*`` (WhatsApp shows the double asterisks literally).
    An unpaired ``*`` or ``_`` is literal text, not formatting, so it is
    escaped by removing it rather than swallowing the rest of the message.
    """
    if not text:
        return text
    # Bold first: collapse the doubled delimiters WhatsApp cannot render.
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"_\1_", text, flags=re.DOTALL)
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"~\1~", text, flags=re.DOTALL)
    text = re.sub(r"(?<![\w*])\*(?!\*)(?=\S)(.+?)(?<=\S)\*(?!\*)(?![\w*])", r"*\1*", text,
                  flags=re.DOTALL)
    return text


def fix_word_spacing(text: str) -> str:
    """Repair text that arrived without proper word separation.

    Models routinely emit ``appointment.Confirm`` or ``Monday,at 10:30`` when a
    reply is long. WhatsApp renders that as one unreadable clump, so this puts
    the space back.

    Deliberately conservative — it never touches decimals (``3.5``), times
    (``10:30``), phone numbers, e-mail addresses or URLs, because inserting a
    space there would corrupt real data.
    """
    if not text:
        return text
    out_lines = []
    for line in text.split("\n"):
        # Anything carrying an address/URL/handle is left exactly as-is.
        if "://" in line or "@" in line or re.search(r"\+\d[\d\s\-()]{7,}", line):
            out_lines.append(line)
            continue
        # Space before punctuation: "Monday , at" -> "Monday, at"
        line = re.sub(r"[ \t]+([,.;:!?])", r"\1", line)
        # Missing space after sentence-ending punctuation, only when the next
        # character is a letter. The preceding word must be at least two
        # characters, so single-letter abbreviations ("e.g", "i.e") are left
        # alone instead of being torn into "e. g". Digits count too, because a
        # date or time often ends the sentence ("2026.Please arrive").
        line = re.sub(r"(?<=[A-Za-z0-9]{2}[.!?])(?=[A-Za-z])", " ", line)
        # Collapse a space that ended up duplicated by the rules above.
        line = re.sub(r"([.!?])[ \t]{2,}(?=[A-Za-z])", r"\1 ", line)
        out_lines.append(line)
    return "\n".join(out_lines)


# A clock time, and the common date spellings. Deliberately narrow: these are
# the two details a customer scans a booking message for.
_TIME_RE = re.compile(
    r"(?<![\w:])\d{1,2}:\d{2}\s*[AaPp]\.?[Mm](?![\w])"
)
_DATE_RE = re.compile(
    r"(?<![\w])"
    r"(?:\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|"
    r"Sep|Sept|Oct|Nov|Dec)\.?\s+\d{4}"
    r"|(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|"
    r"Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    r"(?![\w])"
)
_BOLD_SPAN_RE = re.compile(r"\*[^*\n]+\*")


def emphasize_key_details(text: str) -> str:
    """Bold the details a customer is looking for: dates and times.

    Scoped tightly on purpose. Every extra thing wrapped in asterisks makes the
    whole message shout, so only a clock time or a written date is emphasised —
    never department names, prices or whole sentences, which are left for the
    message author to bold deliberately.

    Text already inside ``*asterisks*`` is skipped so an existing bold span is
    never nested or corrupted.
    """
    if not text:
        return text

    def _emphasise_plain(chunk: str) -> str:
        if "*" in chunk:
            # Unbalanced/partial bold: leave it completely alone.
            return chunk
        chunk = _TIME_RE.sub(lambda m: f"*{m.group(0).strip()}*", chunk)
        chunk = _DATE_RE.sub(lambda m: f"*{m.group(0).strip()}*", chunk)
        return chunk

    parts: list = []
    last = 0
    for match in _BOLD_SPAN_RE.finditer(text):
        parts.append(_emphasise_plain(text[last : match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_emphasise_plain(text[last:]))
    return "".join(parts)


def normalize_spacing(text: str) -> str:
    """Give the message room to breathe.

    * trailing whitespace removed per line
    * more than one blank line collapsed to a single blank line
    * runs of 3+ spaces collapsed (keeps intentional single spaces intact)
    * leading/trailing blank lines stripped
    """
    if not text:
        return text
    lines = [line.rstrip() for line in text.split("\n")]
    out: list = []
    blank_run = 0
    for line in lines:
        if not line.strip():
            blank_run += 1
            if blank_run > 1:
                continue
            out.append("")
        else:
            blank_run = 0
            out.append(line)
    text = "\n".join(out)
    text = re.sub(r"[ \t]{3,}", "  ", text)
    return text.strip("\n")


def normalize_bullets(text: str) -> str:
    """Give list items a consistent, readable marker and indent."""
    if not text:
        return text
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        match = re.match(r"^[-*+•]\s+(.*)$", stripped)
        if match:
            lines.append(f"• {match.group(1).strip()}")
        else:
            match_num = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
            if match_num:
                lines.append(f"{match_num.group(1)}. {match_num.group(2).strip()}")
            else:
                lines.append(line)
    return "\n".join(lines)


def split_long_paragraph(text: str, *, max_len: int = 900) -> str:
    """Insert a paragraph break in very long unbroken paragraphs.

    WhatsApp renders a wall of text poorly. Breaks are only placed at sentence
    boundaries so no sentence is cut in half.
    """
    if not text or len(text) <= max_len:
        return text
    out_lines = []
    for line in text.split("\n"):
        if len(line) <= max_len:
            out_lines.append(line)
            continue
        # Break after sentence-ending punctuation, greedily.
        remaining = line
        while len(remaining) > max_len:
            window = remaining[:max_len]
            cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
            if cut <= 0:
                break
            out_lines.append(remaining[: cut + 1].strip())
            remaining = remaining[cut + 1 :].strip()
        if remaining:
            out_lines.append(remaining)
    return "\n\n".join(out_lines)


def separate_list_blocks(text: str) -> str:
    """Put a list on its own block, separated from the prose above it.

    A bullet list butted straight up against a paragraph reads as one clump.
    A single blank line is inserted before the FIRST item of each run only —
    items inside a run stay tight together, because spacing out every bullet
    makes a three-item list look like three separate thoughts.
    """
    if not text:
        return text
    lines = text.split("\n")
    out: list = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        is_item = bool(
            re.match(r"^[-*+•]\s+\S", stripped) or re.match(r"^\d+[.)]\s+\S", stripped)
        )
        if is_item:
            if not in_list and out and out[-1].strip():
                out.append("")
            in_list = True
        else:
            in_list = False
        out.append(line)
    return "\n".join(out)


def _unbold_line(line: str) -> str:
    """Drop bold markers but keep the line readable as plain text.

    A span like ``*Full Name:* Tara`` becomes ``Full Name: Tara``. The colon
    already carries the label/value break, so the emphasis was adding nothing
    the customer could act on -- and a line that is half asterisks reads as
    machine output rather than a person replying.
    """
    if "*" not in line:
        return line
    out = re.sub(r"\*+", "", line)
    # ``*Label:* value`` -> ``Label: value``. The colon must be followed by a
    # letter, which keeps a value's own colon intact -- without this guard a
    # time like 9:00 became "9: 00" and a URL lost its scheme separator.
    out = re.sub(r"([A-Za-z0-9)])\s{0,2}:(?=[A-Za-z])", r"\1: ", out)
    return re.sub(r"[ \t]{2,}", " ", out).rstrip()


def _soft_wrap_line(line: str, width: int) -> list:
    """Wrap one logical line to ``width`` at word boundaries.

    Only applied to prose. Anything that must stay on one line to make sense
    -- bullet items, ``Label: value`` pairs, a URL, a date or time range --
    is left alone, because wrapping those makes them harder to read.

    A phone number or e-mail address does *not* block wrapping: a phone number
    is a single whitespace-free token, so it moves to the next line whole. Only
    a URL or an e-mail is truly unsplittable, and those are excluded.
    """
    if len(line) <= width:
        return [line]
    stripped = line.strip()
    # Structural lines are never wrapped.
    if re.match(r"^[-*+•]\s+\S", stripped):
        return [line]
    if re.match(r"^[\w][\w ()/&.'-]{0,40}:\s*\S", stripped):
        return [line]
    # A URL or e-mail must not be split; a phone number is safe to move whole.
    if "://" in stripped or re.search(r"\S+@\S+\.\S+", stripped):
        return [line]
    # A label:value line with several pairs on one row (a booking summary).
    if stripped.count(": ") >= 2:
        return [line]
    # No spaces to break on: leave it, splitting a URL or a token is worse.
    if " " not in stripped:
        return [line]

    # Wrap between words, but keep a value that belongs together whole: a
    # time (9:00), an ISO date (2026-10-07) or a written date (29 September
    # 2026) moves to the next line as a unit. Splitting "29 September" from
    # "2027" leaves the customer reading a date that looks truncated.
    _GLUED = re.compile(
        r"(\d{4}-\d{2}-\d{2}"
        r"|\d{1,2}:\d{2}"
        r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})"
        r"$",
        re.IGNORECASE,
    )
    units: list = []
    for word in stripped.split():
        if units and _GLUED.search(units[-1]):
            units[-1] = f"{units[-1]} {word}"
        else:
            units.append(word)

    lines: list = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = unit
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def plain_text_finalize(text: str, *, width: int = 58) -> str:
    """Final pass: remove bold markers and wrap long prose lines.

    Runs last in the pipeline so it also cleans up the asterisks that
    ``emphasize_key_details`` adds, which is the main source of them.

    Wrapping is deliberately conservative -- a narrower limit than a web page
    because a phone shows about 40-45 characters per line, and a bullet or a
    ``Label: value`` row is left intact because breaking it would make it
    harder to read, not easier.
    """
    if not text:
        return text
    out_lines: list = []
    for raw_line in text.split("\n"):
        line = _unbold_line(raw_line)
        out_lines.extend(_soft_wrap_line(line, width))
    return "\n".join(out_lines)


def format_outbound(raw: Optional[str]) -> str:
    """Full outbound formatting pipeline. Safe on None and on plain prose."""
    if raw is None:
        return ""
    text = str(raw)
    if not text.strip():
        return ""
    try:
        from app.engine.user_visible_text import sanitize_user_visible_text

        cleaned = sanitize_user_visible_text(text)
        if isinstance(cleaned, str) and cleaned.strip():
            text = cleaned
    except Exception:
        pass
    text = strip_leaked_headers(text)
    text = fix_word_spacing(text)
    text = normalize_bold(text)
    text = normalize_bullets(text)
    text = separate_list_blocks(text)
    text = emphasize_key_details(text)
    text = normalize_spacing(text)
    text = split_long_paragraph(text)
    text = plain_text_finalize(text)
    return text.strip()
