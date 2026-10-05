"""BookingDirective is a web-widget concept. WhatsApp is pure conversation.

Product rule: no directive machinery on WhatsApp. The customer types a date, a
time and "yes"; the engine reads it back. This locks that rule in, and covers
the validation that used to depend on a directive row.
"""

import inspect
from types import SimpleNamespace

import pytest

from app.core.whatsapp_client import format_action_for_whatsapp_text_only

WA_CHANNELS = ("whatsapp", "whatsapp_cloud")


def _action(directive, message="ok", field_meta=None):
    return SimpleNamespace(
        widget_directive=directive,
        message=message,
        field_meta=field_meta or {},
    )


# â”€â”€ no interactive output is possible on WhatsApp â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@pytest.mark.parametrize(
    "directive",
    [
        "REQUIRE_DATE_PICKER",
        "REQUIRE_TIME_SLOT_GRID",
        "REQUIRE_SELECT",
        "SELECT_DROPDOWN",
        "REQUEST_CONFIRMATION",
        "BOOKING_SUMMARY_CARD",
        "REQUIRE_OTP_MODAL",
        "TEXT_INPUT",
    ],
)
def test_never_emits_interactive_elements(directive) -> None:
    """The old failure mode: with no directive row the transports fell
    through to format_action_for_whatsapp, which returns an interactive list
    whenever the field carries options -- so removing directives would have
    started sending WhatsApp buttons."""
    out = format_action_for_whatsapp_text_only(
        _action(directive, field_meta={"options": ["Thu, Oct 01", "Fri, Oct 02"]})
    )
    assert out["type"] == "text", directive
    assert "sections" not in out
    assert "buttons" not in out


def test_date_options_are_not_listed_to_the_customer() -> None:
    """The date step states the booking window in words. A list of day names
    reads as a menu, and any day on it can still turn out to be full, so the
    customer picks from the menu and is then told the day is unavailable."""
    out = format_action_for_whatsapp_text_only(
        _action("REQUIRE_DATE_PICKER", "Which date?", {"options": ["Thu, Oct 01"]})
    )
    assert out["type"] == "text"
    assert "Thu, Oct 01" not in out["body"]
    assert "Available dates" not in out["body"]


def test_dict_shaped_options_are_rendered_too() -> None:
    out = format_action_for_whatsapp_text_only(
        _action(
            "REQUIRE_TIME_SLOT_GRID",
            "Pick a time",
            {"options": [{"label": "9:00 AM"}, {"label": "3:00 PM"}]},
        )
    )
    assert "9:00 AM" in out["body"]
    assert "3:00 PM" in out["body"]


def test_confirm_step_stays_text_with_worded_options() -> None:
    out = format_action_for_whatsapp_text_only(_action("REQUEST_CONFIRMATION", "Book it?"))
    assert out["type"] == "text"
    assert out["body"] == "Book it?"
    assert "buttons" not in out
    # No transport-appended hint: customer and transcript see identical text.
    assert "Reply CONFIRM to confirm" not in out["body"]


def test_time_slots_are_still_listed() -> None:
    """Only the date list was dropped. The customer has to see the time slots
    to reply with one."""
    out = format_action_for_whatsapp_text_only(
        _action("REQUIRE_TIME_SLOT_GRID", "Pick a time", {"options": ["9:00 AM"]})
    )
    assert "9:00 AM" in out["body"]


# â”€â”€ the orchestrator must not persist directives for WhatsApp â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_orchestrator_does_not_persist_directives_for_whatsapp() -> None:
    """The booking-step block still runs on WhatsApp -- it computes the slot
    list, rejects an unavailable time and frames the step -- but it must not
    write a BookingDirective row or an active_directive_id.

    Asserted on the session-state write rather than on the presence of the
    create call, because a source-text check on the call proved nothing about
    behaviour and initially passed while the row was still being written.
    """
    from app.engine import langgraph_orchestrator as orch

    source = inspect.getsource(orch)
    # nothing may put a directive id into session state
    assert '_cd["active_directive_id"]' not in source
    assert 'collected_data["active_directive_id"]' not in source
    # no directive row is created anywhere in the orchestrator (the name may
    # still appear in a comment explaining why)
    assert "create_directive as _create_dir" not in source
    assert "create_directive(" not in source
    assert "_directive_created" not in source
    # no dead-code guard left behind
    assert "if False:" not in source
    # and the functional WhatsApp step behaviour is still present
    assert "_unmatched_time_choice" in source
    # the channel flag survives for genuinely text-only WhatsApp behaviour
    assert '_is_whatsapp = str(getattr(chat_session, "channel", "")).lower()' in source


def test_booking_directive_service_has_no_runtime_caller() -> None:
    """create_directive's only caller was the WhatsApp-only block just
    removed, so the persisted-directive layer is now unreferenced at runtime.
    The web widget renders from the ActionPayload widget_directive and
    field_meta, so this is not a functional regression -- but it means
    booking_directive/service.py is dead code, not a live web path."""
    import app.api.v1.endpoints.whatsapp_qr as qr
    import app.api.v1.endpoints.whatsapp_webhook as cloud
    from app.engine import langgraph_orchestrator as orch
    from app.engine.booking_directive import service

    assert hasattr(service, "create_directive")  # API retained
    for module in (orch, qr, cloud):
        source = inspect.getsource(module)
        assert "create_directive(" not in source, module.__name__


def test_web_widget_renders_from_the_action_payload_not_a_directive_row() -> None:
    """Directives survive for the web widget -- but not as DB rows. The widget
    builds its calendar and confirm controls from the ActionPayload
    widget_directive and field_meta, so no BookingDirective row is involved."""
    from app.core.whatsapp_client import format_action_for_whatsapp

    out = format_action_for_whatsapp(
        _action("REQUIRE_DATE_PICKER", "Pick", {"options": ["Thu, Oct 01"]})
    )
    # the widget-facing formatter still produces interactive structures
    assert out["type"] == "interactive_list"
    assert out["sections"][0]["rows"][0]["title"] == "Thu, Oct 01"


# â”€â”€ validation must not depend on a directive row â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@pytest.mark.parametrize("module", ["whatsapp_qr", "whatsapp_webhook"])
def test_both_transports_validate_typed_dates_without_a_directive(module) -> None:
    """validate_typed_date_availability had exactly one caller region, inside
    the directive block. Removing directives without this would have let a
    customer book a date with no free slot."""
    mod = __import__(
        f"app.api.v1.endpoints.{module}", fromlist=["x"]
    )
    source = inspect.getsource(mod)
    assert "validate_typed_date_availability" in source, module
    assert "validate_typed_date(" in source, module
    # and it must be keyed off session state, not a directive
    assert "missing_fields or [None])[0]" in source, module
    assert '== "date"' in source, module
    # corrections state the window in words, not a list of days
    assert "_format_available_dates_for_prompt as _dfg" in source, module
    assert "Available dates: " not in source, module


@pytest.mark.parametrize("module", ["whatsapp_qr", "whatsapp_webhook"])
def test_both_transports_use_the_text_only_formatter(module) -> None:
    mod = __import__(
        f"app.api.v1.endpoints.{module}", fromlist=["x"]
    )
    source = inspect.getsource(mod)
    assert "format_action_for_whatsapp_text_only" in source, module
    # the interactive-capable formatter must not be the transport's terminal step
    terminal = source[source.rindex("wa_msg = whatsapp_client") :]
    assert "text_only" in terminal, module


# â”€â”€ session state must not carry directive keys â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_available_dates_work_with_no_directive_options() -> None:
    from app.engine.scheduling_policy import select_date_option_labels

    import asyncio

    async def _run():
        return await select_date_option_labels(
            db=None,
            org_id="9516bfcf-437f-4eda-842e-067194a64dcb",
            org_timezone="UTC",
            directive_options=None,
        )

    # No directive and no db: empty is correct, and must not raise.
    assert asyncio.run(_run()) == []


# -- a conversational turn is not a malformed date -------------------------
def test_date_guard_only_corrects_a_real_date_attempt() -> None:
    """Regression found live: with the guard correcting unconditionally, "go
    on" at the date step was answered "Please type the meeting date." --
    lecturing someone about date format because they said "go on". The
    correction must require an actual date token in the message."""
    import inspect

    from app.api.v1.endpoints import whatsapp_qr, whatsapp_webhook

    for module in (whatsapp_qr, whatsapp_webhook):
        source = inspect.getsource(module)
        assert "_contains_concrete_date_value as" in source, module.__name__
        assert "_has_date_token(text)" in source, module.__name__


@pytest.mark.parametrize("text", ["go on", "still there?", "hello", "thanks", "ok"])
def test_conversational_turns_carry_no_date_token(text) -> None:
    from app.engine.scheduling_policy import _contains_concrete_date_value

    assert _contains_concrete_date_value(text.lower()) is False


@pytest.mark.parametrize(
    "text", ["1 oct", "01/10/2026", "2026-10-01", "next friday", "tomorrow"]
)
def test_date_attempts_carry_a_date_token(text) -> None:
    from app.engine.scheduling_policy import _contains_concrete_date_value

    assert _contains_concrete_date_value(text.lower()) is True


# -- the offered dates must be bookable, not raw calendar days -------------
def test_date_step_uses_the_window_in_words() -> None:
    """The date step must state the horizon and the closed-day note, in the
    format the customer saw before, rather than listing days."""
    import inspect

    from app.engine import langgraph_orchestrator as orch

    source = inspect.getsource(orch)
    assert "_format_available_dates_for_prompt as _fmt_wa_date" in source
    assert "DATE_STEP_FORMAT_APPLIED" in source
    assert "Select your preferred date" in source
    # and the day-list injection must be gone
    assert "DATE_LIST_INJECTED" not in source


# -- internal prompt headers must not reach the customer -------------------
def test_prompt_header_with_annotation_is_stripped() -> None:
    """Pre-existing leak, seen live: the reply opened with
    "BUSINESS HOURS (authoritative, answer timing questions from this)". The
    ALL-CAPS check rejected the line because of the lowercase annotation, so
    the header list was never consulted and the whole line was sent."""
    from app.core.wa_text import format_outbound, strip_leaked_headers

    raw = (
        "BUSINESS HOURS (authoritative, answer timing questions from this)\n"
        "Got it. What date would you like to come in?"
    )
    assert "BUSINESS HOURS" not in strip_leaked_headers(raw)
    assert "BUSINESS HOURS" not in format_outbound(raw)
    assert "What date would you like" in format_outbound(raw)


@pytest.mark.parametrize(
    "line",
    ["BUSINESS HOURS", "OUR SERVICES (use for pricing)", "FAQ (short answers)"],
)
def test_known_headers_still_stripped_with_or_without_annotation(line) -> None:
    from app.core.wa_text import strip_leaked_headers

    assert strip_leaked_headers(f"{line}\nHello there") == "Hello there"


@pytest.mark.parametrize(
    "text",
    [
        "Hello! How can I help?",
        "Our services include physiotherapy.",
        "Sure, here are the available dates.",
    ],
)
def test_normal_replies_are_untouched(text) -> None:
    from app.core.wa_text import strip_leaked_headers

    assert strip_leaked_headers(text) == text

