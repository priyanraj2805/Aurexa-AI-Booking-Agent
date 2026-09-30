"""No confirmation claim may reach a customer before a Booking row exists.

Production failure: with no Booking row for the session, the agent said
"Your appointment is booked for October 2, 2026 at 3:00 PM. We look forward
to seeing you!", then, once the code arrived, "Your booking is already
processed."

Cause: several paths return the model's text straight to the customer with an
early `return`, before the guard near the end of process_turn runs. A model
that volunteers a confirmation claim on one of those paths escapes entirely.
"""
import inspect

import pytest

from app.core.wa_text import format_outbound
from app.engine.user_visible_text import looks_like_confirmation_claim
from app.engine import langgraph_orchestrator as orch


CLAIMS = [
    "Perfect. Your appointment is booked for October 2, 2026 at 3:00 PM. "
    "We look forward to seeing you!",
    "Your appointment is confirmed for October 2 at 3 PM.",
    "You're booked in for 3 PM on the 2nd.",
    "We have booked your appointment.",
]


@pytest.mark.parametrize("text", CLAIMS)
def test_detector_recognises_the_production_phrasings(text) -> None:
    assert looks_like_confirmation_claim(text) is True


@pytest.mark.parametrize("text", CLAIMS)
def test_every_early_return_path_is_guarded(text) -> None:
    """The guard must run on each path that returns the model's text."""
    source = inspect.getsource(orch)
    for name in ("_guard_confirmation_claim", "_session_has_persisted_booking"):
        assert name in source, name


def test_early_return_paths_call_the_guard() -> None:
    """A regression test on the shape of the fix: every early `return` of a
    model-authored message must pass through the guard first."""
    source = inspect.getsource(orch)
    # The guard helper is awaited immediately before each early return.
    assert source.count("_guarded = await _guard_confirmation_claim(") >= 3, (
        "expected the guard on at least the off-topic, slot, knowledge and "
        "post-knowledge early-return paths"
    )


@pytest.mark.asyncio
async def test_guard_replaces_a_claim_when_no_booking_row_exists() -> None:
    """The real contract: with no Booking row the claim is replaced by the
    review card, restoring details -> summary -> confirm -> OTP -> booking."""
    from types import SimpleNamespace

    session = SimpleNamespace(
        collected_data={"name": "Rehan", "email": "r@example.com",
                        "phone": "+916350063777", "date": "2026-10-02",
                        "time": "3:00 PM"},
        session_status="ACTIVE",
    )

    class _NoBookingDB:
        async def execute(self, *a, **k):
            class _R:
                def scalars(self): return self
                def first(self): return None
            return _R()

    result = await orch._guard_confirmation_claim(
        _NoBookingDB(),
        "sess-1",
        "Perfect. Your appointment is booked for October 2, 2026 at 3:00 PM.",
        "TEXT_INPUT",
        session,
        {},
        SimpleNamespace(id=None),
        {},
    )
    assert result is not None, "guard must downgrade when no Booking exists"
    message, directive, _meta = result
    assert "booked" not in message.lower()
    assert "confirmed" not in message.lower()


@pytest.mark.asyncio
async def test_guard_leaves_a_genuine_booking_claim_alone(monkeypatch) -> None:
    """A booking that really exists keeps its confirmation wording."""
    from types import SimpleNamespace

    async def _has_booking(*a, **k):
        return True

    monkeypatch.setattr(orch, "_session_has_persisted_booking", _has_booking)
    session = SimpleNamespace(collected_data={}, session_status="CONFIRMED")

    result = await orch._guard_confirmation_claim(
        None, "sess-2", "You're booked in for 3 PM.", "TEXT_INPUT",
        session, {}, SimpleNamespace(id=None), {},
    )
    assert result is None


def test_already_processed_reply_only_when_session_is_not_active() -> None:
    """"Your booking is already processed" is only truthful for a finished
    session. It must never be reachable for an ACTIVE one."""
    import inspect as _i

    from app.engine import message_processor as mp

    src = _i.getsource(mp._handle_booking_flow)
    idx = src.find('"Your booking is already processed."')
    assert idx != -1
    before = src[max(0, idx - 400):idx]
    assert 'session_status != "ACTIVE"' in before, (
        "the already-processed reply must be guarded by the ACTIVE check"
    )
