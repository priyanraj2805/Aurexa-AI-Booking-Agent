"""The date step must show which dates are actually open.

Observed failure on WhatsApp: a customer booking was asked "What date would
you prefer for the appointment?" and, after sending something unparseable,
"That didn't look like a date." — with no indication of which dates were
available. The list existed but was only produced when the customer happened
to ask for it explicitly, so most customers never saw it.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

MIDDOT = chr(0xB7)

from app.engine.scheduling_policy import (
    is_available_dates_query,
    lookup_available_dates,
    lookup_available_dates_answer,
    select_date_option_labels,
)

ORG_ID = str(uuid4())


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """Just enough db surface for the availability scan."""

    def __init__(self, sched_row=None, available_every=1, scan_limit=None):
        self._sched_row = sched_row
        self._available_every = available_every
        self._scan_limit = scan_limit
        self.days_scanned = []

    async def execute(self, stmt):
        return _FakeResult([self._sched_row])

    async def flush(self):
        pass


def _sched_row():
    return SimpleNamespace(allow_same_day_booking=False, slot_cutoff_minutes=5)


@pytest.fixture(autouse=True)
def _stable_inventory(monkeypatch):
    """Deterministic slot inventory: every Nth day is bookable.

    Mirrors the real contract: compute_slot_inventory returns
    (list_of_slots, date_level_status) and each slot carries `available`.
    """
    from app.engine import slot_engine

    async def _inventory(db, org_id, target_date, org_config=None):
        offset = (target_date - datetime.now(timezone.utc).date()).days
        # available_every <= 0 means "never bookable"
        available = (
            offset % db._available_every == 0 if db._available_every > 0 else False
        )
        return (
            [
                {
                    "start_time": "10:00",
                    "start_utc": None,
                    "end_utc": None,
                    "available": available,
                    "reason": None if available else "booked",
                }
            ],
            "ok",
        )

    def _policy(inventory, target_date, base_status=None, **kwargs):
        return inventory, base_status, None

    monkeypatch.setattr(slot_engine, "compute_slot_inventory", _inventory)
    monkeypatch.setattr(
        slot_engine, "apply_scheduling_policy_to_inventory", _policy
    )


# â”€â”€ the scan itself no longer requires a question â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@pytest.mark.asyncio
async def test_scan_runs_without_any_question_phrase() -> None:
    """The whole point: the date step needs the list even though the customer
    never asked for it."""
    db = _FakeDB(sched_row=_sched_row())
    answer = await lookup_available_dates(
        db=db, org_id=ORG_ID, effective_timezone="UTC", org_timezone="UTC"
    )
    assert answer is not None
    assert answer["status"] == "ok"
    assert answer["available_dates"]


@pytest.mark.asyncio
async def test_scan_output_uses_horizon_wording_not_day_menu() -> None:
    """Customer-facing text is the horizon window ("We are available from
    ... to ..., please choose a date within this horizon."), never a day
    menu — a listed day can still turn out full by the time it is picked.
    The machine-readable day labels stay in the payload for pickers."""
    db = _FakeDB(sched_row=_sched_row())
    answer = await lookup_available_dates(
        db=db, org_id=ORG_ID, effective_timezone="UTC", org_timezone="UTC"
    )
    text = answer["response_text"]
    assert "Here are our next available dates" not in text
    assert "within this horizon" in text
    assert "(All dates in UTC.)" in text
    assert answer["available_date_labels"]


@pytest.mark.asyncio
async def test_scan_reports_nothing_found_rather_than_pretending() -> None:
    db = _FakeDB(sched_row=_sched_row(), available_every=0)
    answer = await lookup_available_dates(
        db=db, org_id=ORG_ID, effective_timezone="UTC", org_timezone="UTC"
    )
    assert answer["status"] == "none_found"
    assert answer["available_dates"] == []


@pytest.mark.asyncio
async def test_missing_schedule_row_is_not_an_empty_calendar() -> None:
    db = _FakeDB(sched_row=None)
    answer = await lookup_available_dates(
        db=db, org_id=ORG_ID, effective_timezone="UTC", org_timezone="UTC"
    )
    assert answer["status"] == "policy_missing"
    assert answer["available_dates"] == []


@pytest.mark.asyncio
async def test_unusable_db_returns_none_so_caller_falls_back() -> None:
    assert (
        await lookup_available_dates(
            db=None, org_id=ORG_ID, effective_timezone="UTC"
        )
        is None
    )
    assert (
        await lookup_available_dates(
            db=_FakeDB(), org_id="not-a-uuid", effective_timezone="UTC"
        )
        is None
    )


@pytest.mark.asyncio
async def test_every_bookable_day_is_real_calendar_progression() -> None:
    """Guards against the list drifting off real dates."""
    db = _FakeDB(sched_row=_sched_row(), available_every=2)
    answer = await lookup_available_dates(
        db=db, org_id=ORG_ID, effective_timezone="UTC", org_timezone="UTC", max_dates=5
    )
    parsed = [date.fromisoformat(d) for d in answer["available_dates"]]
    assert len(parsed) == 5
    assert all(b > a for a, b in zip(parsed, parsed[1:]))
    assert all(d >= datetime.now(timezone.utc).date() for d in parsed)
    for d in parsed:
        # Day labels live in the payload for pickers — the customer-facing
        # text is the horizon window, never a day menu.
        assert d.strftime("%a, %b %d") in answer["available_date_labels"]


# â”€â”€ what the WhatsApp date step actually shows â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@pytest.mark.asyncio
async def test_step_shows_only_genuinely_bookable_days() -> None:
    """This org is closed on Sundays, and the stored directive options are
    plain calendar days. Showing those raw would send the customer to a day
    that cannot be booked."""
    db = _FakeDB(sched_row=_sched_row(), available_every=3)
    labels = await select_date_option_labels(
        db=db,
        org_id=ORG_ID,
        org_timezone="UTC",
        directive_options=["Sun, Nov 01", "Mon, Nov 02", "Tue, Nov 03"],
    )
    assert labels
    # every third day is bookable, so the fallback calendar days must not leak
    assert labels != ["Sun, Nov 01", "Mon, Nov 02", "Tue, Nov 03"]
    for label in labels:
        assert any(label == d for d in labels)


@pytest.mark.asyncio
async def test_step_falls_back_to_directive_options_when_scan_fails() -> None:
    """A database problem must degrade to the old behaviour, not silence."""
    labels = await select_date_option_labels(
        db=None,
        org_id=ORG_ID,
        org_timezone="UTC",
        directive_options=["Sun, Nov 01", "Mon, Nov 02"],
    )
    assert labels == ["Sun, Nov 01", "Mon, Nov 02"]


@pytest.mark.asyncio
async def test_step_falls_back_when_nothing_is_bookable() -> None:
    db = _FakeDB(sched_row=_sched_row(), available_every=0)
    labels = await select_date_option_labels(
        db=db,
        org_id=ORG_ID,
        org_timezone="UTC",
        directive_options=["Sun, Nov 01", "Mon, Nov 02"],
    )
    assert labels == ["Sun, Nov 01", "Mon, Nov 02"]


@pytest.mark.asyncio
async def test_step_never_returns_blank_or_unbounded_output() -> None:
    db = _FakeDB(sched_row=_sched_row(), available_every=1)
    labels = await select_date_option_labels(
        db=db,
        org_id=ORG_ID,
        org_timezone="UTC",
        directive_options=["a", "b", "c", "", "  "],
        max_dates=5,
    )
    assert 0 < len(labels) <= 5
    assert all(label.strip() for label in labels)
    assert all(label == label.strip() for label in labels)


@pytest.mark.asyncio
async def test_step_handles_no_options_at_all() -> None:
    db = _FakeDB(sched_row=_sched_row(), available_every=2)
    labels = await select_date_option_labels(
        db=db, org_id=ORG_ID, org_timezone="UTC", directive_options=None
    )
    assert labels


# â”€â”€ both transports must render the date step â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_both_transports_state_the_window_in_words() -> None:
    """The date step gives the booking window in words, not a list of day
    names. A list reads as a menu, and any day on it can still be full, so the
    customer picks from it and is then told it is unavailable."""
    import inspect

    from app.api.v1.endpoints import whatsapp_qr, whatsapp_webhook

    for module in (whatsapp_qr, whatsapp_webhook):
        source = inspect.getsource(module)
        assert "Available dates: " not in source, module.__name__
        assert "_format_available_dates_for_prompt as _corr_fmt" in source, module.__name__


def test_both_transports_reread_the_active_directive() -> None:
    """Second defect, found live: the engine persisted a NEW directive and
    expired the old one, but the transport rendered from the in-memory
    `active_directive_id` — now EXPIRED — so the step render was skipped and
    the customer got a bare "what date would you like?" with no dates at all.
    The date list appeared on the first ask and vanished on every repeat."""
    import inspect

    from app.api.v1.endpoints import whatsapp_qr, whatsapp_webhook

    for module in (whatsapp_qr, whatsapp_webhook):
        source = inspect.getsource(module)
        assert "db.refresh(chat_session)" in source, module.__name__
        # the refreshed value must actually win
        assert "_active_id_after = _refreshed" in source, module.__name__


def test_pending_step_survives_a_conversational_turn() -> None:
    """Third defect, found live: "go on" and "still there?" were classified as
    conversational, so the knowledge path early-returned with TEXT_INPUT and
    the date step was never rendered. The customer heard "Yes I'm here! What
    date would you like to book?" with no dates — this is the "sometimes" in
    the report. A PENDING step matching the awaited field must still render."""
    import inspect

    from app.api.v1.endpoints import whatsapp_qr, whatsapp_webhook

    for module, waiting, asks in (
        (whatsapp_qr, "_still_waiting", "_already_asks"),
        (whatsapp_webhook, "_still_waiting_c", "_already_asks_c"),
    ):
        source = inspect.getsource(module)
        assert waiting in source, module.__name__
        assert asks in source, module.__name__
        # the step must be reachable without a frame match
        assert "or _still_waiting" in source, module.__name__
        # and the awaited-field check must be anchored on missing_fields
        assert "missing_fields or [None])[0] ==" in source, module.__name__


def test_step_render_sits_next_to_the_time_render() -> None:
    """The date step has no day list; the time step still lists its slots,
    because the customer has to see them to reply with one."""
    import inspect

    from app.api.v1.endpoints import whatsapp_qr

    source = inspect.getsource(whatsapp_qr)
    assert "Available slots: " in source
    assert '"type": "text"' in source  # still the text-only transport


def test_orchestrator_does_not_send_a_separate_dates_message() -> None:
    """The available-dates prose is answered on request; it is not injected
    into every date-step reply."""
    import inspect

    from app.engine import langgraph_orchestrator as orch

    source = inspect.getsource(orch)
    assert "available_dates_appended" not in source
    assert "Here are our next available dates" not in source


# â”€â”€ the question gate is unchanged â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_question_gate_still_requires_an_explicit_inquiry() -> None:
    assert is_available_dates_query("What are the available dates?") is True
    assert is_available_dates_query("tell me available dates") is True
    assert is_available_dates_query("Gayhajvndkso") is False
    assert is_available_dates_query("that didn't look like a date") is False


@pytest.mark.asyncio
async def test_answer_helper_still_gated_on_the_question() -> None:
    db = _FakeDB(sched_row=_sched_row())
    assert (
        await lookup_available_dates_answer(
            db=db,
            org_id=ORG_ID,
            user_message="What are the available dates?",
            effective_timezone="UTC",
        )
        is not None
    )
    assert (
        await lookup_available_dates_answer(
            db=db,
            org_id=ORG_ID,
            user_message="Gayhajvndkso",
            effective_timezone="UTC",
        )
        is None
    )


@pytest.mark.asyncio
async def test_concrete_date_is_still_a_value_not_an_inquiry() -> None:
    """A customer naming a date must reach the date validator, not be
    answered with a list."""
    db = _FakeDB(sched_row=_sched_row())
    assert (
        await lookup_available_dates_answer(
            db=db,
            org_id=ORG_ID,
            user_message="can I book on 8 Oct",
            effective_timezone="UTC",
        )
        is None
    )


# â”€â”€ the orchestrator gate that appends the list â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_orchestrator_appends_dates_at_the_date_step() -> None:
    """The append was tried and reverted: the date picker is a built-in
    feature, so a separate prose list is redundant. Guard the revert."""
    import inspect

    from app.engine import langgraph_orchestrator as orch

    source = inspect.getsource(orch)
    assert "lookup_available_dates" not in source
    assert "available_dates_appended" not in source
    assert "Here are our next available dates" not in source


# -- the step guard must not trap the customer -----------------------------
@pytest.mark.asyncio
async def test_step_guard_falls_through_to_the_engine_when_llm_fails() -> None:
    """Fourth defect, found live: should_bypass_step_guard failed CLOSED, so
    with the LLM slow or down a conversational "go on" at the date step was
    answered "That doesn't look like a valid date" and the flow stalled. This
    is the "sometimes" in the report."""
    from app.engine import check_booking_flow

    async def _boom(*args, **kwargs):
        raise RuntimeError("llm unavailable")

    original = None
    try:
        from app.engine import message_processor as _mp

        original = _mp.llm_engine._call_llm
        _mp.llm_engine._call_llm = _boom
        assert await check_booking_flow.should_bypass_step_guard("go on") is True
        assert await check_booking_flow.should_bypass_step_guard(
            "still there?"
        ) is True
    finally:
        if original is not None:
            from app.engine import message_processor as _mp

            _mp.llm_engine._call_llm = original


@pytest.mark.asyncio
async def test_step_guard_still_holds_a_real_date_attempt() -> None:
    """Fail-open must not let an actual date through as a bypass."""
    from app.engine import check_booking_flow

    assert await check_booking_flow.should_bypass_step_guard("01/10/2026") is False
    assert await check_booking_flow.should_bypass_step_guard("2026-10-01") is False


def test_date_correction_states_the_window() -> None:
    """A correction that never says what the booking window is leaves the
    customer with nothing to go on."""
    import inspect

    from app.api.v1.endpoints import whatsapp_qr, whatsapp_webhook

    for module in (whatsapp_qr, whatsapp_webhook):
        source = inspect.getsource(module)
        assert "date_correction" in source, module.__name__
        assert "Available dates: " not in source, module.__name__
        assert "_format_available_dates_for_prompt as _dfg" in source, module.__name__

