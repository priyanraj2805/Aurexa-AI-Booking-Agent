"""Replies must read as a person wrote them, not as markdown."""
import pytest

from app.core.wa_text import format_outbound, plain_text_finalize


# ── no asterisks reach the customer ────────────────────────────────────────
@pytest.mark.parametrize(
    "raw",
    [
        "*Full Name:* Tara",
        "**Bold** and *italic*",
        "Your booking is *confirmed*",
        "Available on *7 Oct 2026*",
        "Please *confirm* to continue",
        "Code: *123456*",
    ],
)
def test_no_asterisks_survive(raw: str) -> None:
    out = format_outbound(raw)
    assert "*" not in out, out


def test_label_value_keeps_its_space() -> None:
    """Removing the asterisks must not turn "Name: Tara" into "Name:Tara"."""
    out = format_outbound("*Full Name:* Tara")
    assert out.strip() == "Full Name: Tara"


def test_bullets_survive_without_asterisks() -> None:
    out = format_outbound("Available slots:\n* 9:00 AM\n* 3:00 PM")
    assert "•" in out
    assert "*" not in out
    assert "9:00 AM" in out and "3:00 PM" in out


# ── long prose is wrapped ──────────────────────────────────────────────────
def test_long_prose_is_wrapped() -> None:
    raw = (
        "Thanks for reaching out to Dotstark. Could you please share your "
        "email address so we can confirm the appointment details with you?"
    )
    out = format_outbound(raw)
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert len(lines) > 1, out
    assert all(len(ln) <= 60 for ln in lines), lines


def test_bullet_items_are_not_wrapped() -> None:
    """A broken bullet is harder to read than a long one."""
    raw = "• 9:00 AM appointment slot on Tuesday available for booking today"
    out = format_outbound(raw)
    assert out.count("\n") == 0, out
    assert "9:00 AM" in out


def test_label_value_rows_are_not_wrapped() -> None:
    raw = "Preferred Date: October 07 2026 with the afternoon consultation slot"
    out = format_outbound(raw)
    assert out.count("\n") == 0, out
    assert "Preferred Date:" in out


def test_urls_and_emails_are_never_broken() -> None:
    raw = "Please reach us at appointments@dotstark.com or visit https://dotstark.com/book"
    out = format_outbound(raw)
    assert "appointments@dotstark.com" in out
    assert "https://dotstark.com/book" in out


# ── the thing the customer actually complained about ───────────────────────
def test_booking_summary_reads_plainly() -> None:
    raw = (
        "📅 *Please review your booking*\n\n"
        "👤 *Full Name:* Tara\n"
        "📧 *Email Address:* tara@example.com\n"
        "📞 *Phone Number:* +918544333988\n\n"
        "Is everything correct?\n\n"
        "Tap *Confirm* to book, *Edit* to change something, or *Cancel* to stop."
    )
    out = format_outbound(raw)
    assert "*" not in out
    assert "Full Name: Tara" in out
    assert "Email Address: tara@example.com" in out
    assert "Is everything correct?" in out
    for ln in out.split("\n"):
        if ln.strip() and not ln.strip().startswith(("👤", "📧", "📞", "📅")):
            assert len(ln) <= 60, ln


def test_horizon_message_is_readable() -> None:
    raw = (
        "We are available from *29 September 2026* to *29 September 2027*, "
        "please choose a date within this horizon."
    )
    out = format_outbound(raw)
    assert "*" not in out
    assert "horizon" in out
    for ln in out.split("\n"):
        assert len(ln) <= 60, ln
    # A date may span a line break, but must never be cut mid-value: no line
    # may end on a bare year or a bare "29 September".
    joined = out.replace("\n", " ")
    assert "29 September 2026" in joined
    assert "29 September 2027" in joined


def test_times_are_never_corrupted_by_the_label_fix() -> None:
    """Regression: the unbold rule once turned 9:00 into "9: 00" by treating
    a time's own colon as a label separator."""
    out = format_outbound("• *9:00 AM*\n• *10:30 AM*\n• *3:00 PM*")
    assert "9:00" in out
    assert "9: 00" not in out
    assert "10:30" in out
    assert "3:00" in out
    assert ": " not in out.replace("Full Name: ", "")


def test_urls_keep_their_scheme_colon() -> None:
    raw = "*Confirmation link:* https://dotstark.com/book/abc123 please open it"
    out = format_outbound(raw)
    assert "https://dotstark.com/book/abc123" in out
    assert "https:/ /" not in out


# ── short messages are untouched ───────────────────────────────────────────
@pytest.mark.parametrize(
    "raw",
    [
        "Thanks! What's your phone number?",
        "Yes",
        "Please enter the 6-digit code.",
    ],
)
def test_short_replies_are_unchanged(raw: str) -> None:
    assert format_outbound(raw).strip() == raw


def test_empty_input_is_safe() -> None:
    assert format_outbound(None) == ""
    assert format_outbound("") == ""
    assert format_outbound("   ") == ""
