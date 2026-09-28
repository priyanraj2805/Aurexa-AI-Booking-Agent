"""Outbound WhatsApp presentation.

Formatting only: word separation, block spacing, selective bold. No reply may
lose or gain meaning, and no real-world value (time ranges, phone numbers,
e-mail addresses, URLs, decimals) may be corrupted.
"""
import pytest

from app.core.wa_text import (
    emphasize_key_details,
    fix_word_spacing,
    format_outbound,
    separate_list_blocks,
)


# ── word spacing ───────────────────────────────────────────────────────

def test_missing_space_between_sentences_is_repaired() -> None:
    assert fix_word_spacing("Your slot is set.It is with Dr Sharma.") == (
        "Your slot is set. It is with Dr Sharma."
    )


def test_space_before_punctuation_is_removed() -> None:
    assert fix_word_spacing("We are open Monday , Tuesday.") == (
        "We are open Monday, Tuesday."
    )


def test_word_spacing_never_breaks_real_values() -> None:
    """Decimals, URLs, e-mail addresses and phone numbers must survive."""
    line = "Call +91 63500 63338 or visit https://dotstark.com/x. Fee is 3.5."
    assert fix_word_spacing(line) == line


def test_abbreviations_are_not_split() -> None:
    assert fix_word_spacing("Bring ID e.g. passport") == "Bring ID e.g. passport"


# ── selective bold ─────────────────────────────────────────────────────

def test_dates_and_times_are_emphasised() -> None:
    out = emphasize_key_details("Your slot is 10:30 AM on 30 September 2026.")
    assert out == "Your slot is *10:30 AM* on *30 September 2026*."


def test_existing_bold_is_preserved_and_not_nested() -> None:
    out = emphasize_key_details("Your *booking* is on 30 September 2026.")
    assert out == "Your *booking* is on *30 September 2026*."


def test_time_range_is_not_mangled() -> None:
    """Regression: an over-broad time regex bolded "10:00 and" and corrupted
    the range, so "9:00 - 10:00 and 4:00" became "9:00 - *10:00 and* 4:00"."""
    assert emphasize_key_details("Hours are 9:00 - 10:00 and 4:00 - 5:00.") == (
        "Hours are 9:00 - 10:00 and 4:00 - 5:00."
    )


def test_sentence_full_stop_is_not_swallowed_by_the_bold() -> None:
    assert emphasize_key_details("Arrive before 9:45 AM.") == "Arrive before *9:45 AM*."


def test_bold_stays_selective_and_does_not_over_apply() -> None:
    """Only dates and times get emphasised. Ordinary prose is untouched, so the
    message does not turn into a wall of asterisks."""
    plain = "Dr Sharma works in Cardiology and charges 500 for the consult."
    assert emphasize_key_details(plain) == plain


# ── block separation ───────────────────────────────────────────────────

def test_list_gets_one_blank_line_before_it_and_stays_tight() -> None:
    out = separate_list_blocks("Choose one:\n- Card\n- Cash\n- UPI")
    assert out == "Choose one:\n\n• Card\n• Cash\n• UPI" or out == (
        "Choose one:\n\n- Card\n- Cash\n- UPI"
    )


def test_blank_line_is_not_inserted_between_items() -> None:
    out = separate_list_blocks("Options:\n- Card\n- Cash")
    body = out.split("Options:")[1]
    assert "Card\n- Cash" in body or "Card\n• Cash" in body


# ── full pipeline ──────────────────────────────────────────────────────

def test_clustered_reply_becomes_readable() -> None:
    out = format_outbound(
        "Your appointment is on September 30, 2026 at 10:30 AM."
        "It is with Dr Sharma in Cardiology.Kindly arrive 10 minutes early."
    )
    assert "*10:30 AM*. It is with" in out
    assert "Cardiology. Kindly arrive" in out
    assert "*10:30 AM*" in out


def test_plain_conversational_prose_is_unchanged() -> None:
    reply = "Thanks Priyanshu. What email address can we reach you at?"
    assert format_outbound(reply) == reply


def test_markdown_bold_becomes_whatsapp_bold() -> None:
    """WhatsApp renders **text** literally; it must collapse to *text*."""
    assert format_outbound("Your **booking** is ready") == "Your *booking* is ready"


def test_full_reply_keeps_values_and_reads_cleanly() -> None:
    out = format_outbound(
        "Hi Priyanshu\n"
        "Your appointment is confirmed for 10:30 AM on 30 September 2026."
        "Please arrive 10 minutes early.\n"
        "Bring:\n"
        "- Photo ID\n"
        "- Your reference number\n"
        "Contact +91 63500 63338 if you need to reschedule."
    )
    # Values intact
    assert "+91 63500 63338" in out
    assert "*10:30 AM*" in out
    assert "*30 September 2026*" in out
    # Readable structure
    assert "confirmed for *10:30 AM* on *30 September 2026*. Please arrive" in out
    # List on its own block, separated from "Bring:"
    assert "Bring:\n\n" in out
    assert "Photo ID" in out and "reference number" in out
    # No doubled blank lines
    assert "\n\n\n" not in out


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
    ],
)
def test_pipeline_is_safe_on_empty_input(raw) -> None:
    assert format_outbound(raw) == ""
