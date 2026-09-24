"""Booking-decline flow: explicit no-booking exits gracefully."""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import decline_flow as flow
from app.models.session import ChatSession


def _state(**over):
    from app.schemas.agent import ChatSessionState

    base = dict(
        session_id=str(uuid.uuid4()),
        org_id=str(uuid.uuid4()),
        channel="web",
        collected_data={},
        missing_fields=["name"],
        current_intent="START_BOOKING",
        session_status="ACTIVE",
    )
    base.update(over)
    return ChatSessionState(**base)


async def _row(db_session: AsyncSession, org_id=None) -> ChatSession:
    row = ChatSession(
        org_id=uuid.UUID(org_id) if org_id else uuid.uuid4(),
        channel="web",
        state_data={},
        missing_fields=["name"],
        current_intent="START_BOOKING",
        session_status="ACTIVE",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


def test_decline_patterns():
    for text in [
        "I don't want to book any appointment",
        "I don't want to boook any appointment can you help with something else",
        "do not want to book",
        "not interested",
        "no need to book",
        "no booking for me",
        "just looking around",
        "changed my mind",
    ]:
        assert flow.is_no_booking_request(text) is True, text


def test_decline_negative():
    for text in [
        "I want to book an appointment",
        "my name is Priyanshu",
        "tell me about dotstark",
        "yes book it",
        "",
        # NOTE: bare "Nooo" USED to be here (field-refusal only), but live
        # WhatsApp transcripts showed users stuck in a name-ask loop replying
        # "Nopee"/"Nopeee". A standalone refusal word is now a graceful
        # decline (re-engage still works via booking words).
        "Nooo tell me about dotstark",
        "No, I love it",
        "note this down",
    ]:
        assert flow.is_no_booking_request(text) is False, text


def test_decline_standalone_no_variants():
    for text in ["no", "No", "NO", "noo", "Nooo", "nope", "Nopee", "NOPEEE",
                 "nah", "Nahh", "NAHHH", "no thanks", "No Thanks"]:
        assert flow.is_no_booking_request(text) is True, text


def test_cancel_phrase_not_decline():
    assert flow.is_no_booking_request("I don't want to cancel my booking") is False


def test_reengage_patterns():
    for text in [
        "ok let's book",
        "actually let's book now",
        "I want to book",
        "start a new booking",
        "book it now",
    ]:
        assert flow.is_reengage_request(text) is True, text
    assert flow.is_reengage_request("tell me about dotstark") is False


def test_carries_booking_value():
    assert flow.carries_booking_value("my email is a@b.com") is True
    assert flow.carries_booking_value("call 9876543210") is True
    assert flow.carries_booking_value("just chatting") is False


def test_field_refusal_explicit():
    for text in [
        "I don't want to share my phone",
        "I wont give my email",
        "will not provide phone number",
        "refuse to share",
        "don't ask",
        "skip it",
    ]:
        assert flow.is_field_refusal(text, ["phone"]) is True, text


def test_field_refusal_bare_negation():
    for text in ["No", "Nooo", "Nope", "Nahhh", "nah", "never", "NO."]:
        assert flow.is_field_refusal(text, ["phone"]) is True, text


def test_field_refusal_negative():
    cases = [
        ("Nooo tell me about dotstark", ["name"]),
        ("I don't want to cancel my booking", ["date"]),
        ("No, I love it", ["phone"]),
        ("Nooo", []),
        ("", ["phone"]),
        ("my name is Priyanshu", ["name"]),
        ("check my appointment", ["date"]),
    ]
    for text, missing in cases:
        assert flow.is_field_refusal(text, missing) is False, text


@pytest.mark.asyncio
async def test_start_decline_sets_flag_and_farewell(db_session: AsyncSession):
    from types import SimpleNamespace

    row = await _row(db_session)
    state = _state(session_id=str(row.id), org_id=str(row.org_id))
    action = await flow.start_decline(
        db_session, SimpleNamespace(name="Acme"), state
    )
    assert "No problem" in action.message
    assert "Acme" in action.message
    assert flow.is_declined(state) is True


@pytest.mark.asyncio
async def test_reengage_clears_on_values(db_session: AsyncSession):
    row = await _row(db_session)
    state = _state(
        session_id=str(row.id),
        org_id=str(row.org_id),
        collected_data={flow.BOOKING_DECLINED_KEY: True},
    )
    assert await flow.maybe_reengage(db_session, state, "a@b.com") is True
    assert flow.is_declined(state) is False


@pytest.mark.asyncio
async def test_reengage_ignores_chitchat(db_session: AsyncSession):
    row = await _row(db_session)
    state = _state(
        session_id=str(row.id),
        org_id=str(row.org_id),
        collected_data={flow.BOOKING_DECLINED_KEY: True},
    )
    assert await flow.maybe_reengage(db_session, state, "tell me more") is False
    assert flow.is_declined(state) is True


def _refusal_state(row, missing, collected=None):
    return _state(
        session_id=str(row.id),
        org_id=str(row.org_id),
        collected_data=dict(collected or {}),
        missing_fields=list(missing),
    )


@pytest.mark.asyncio
async def test_field_refusal_soft_asks_same_field(db_session: AsyncSession):
    row = await _row(db_session)
    state = _refusal_state(row, ["phone"])
    action = await flow.maybe_handle_field_refusal(
        db_session, None, state, "Nooo"
    )
    assert action is not None
    assert "phone" in action.message.lower()
    assert "date" not in action.message.lower()
    # State untouched: still missing phone, nothing collected.
    assert state.missing_fields == ["phone"]
    assert state.collected_data == {}


@pytest.mark.asyncio
async def test_field_refusal_offers_contact_choice(db_session: AsyncSession):
    row = await _row(db_session)
    state = _refusal_state(row, ["email"])
    action = await flow.maybe_handle_field_refusal(
        db_session, None, state, "I don't want to share"
    )
    assert action is not None
    assert "phone" in action.message.lower()


@pytest.mark.asyncio
async def test_field_refusal_ignores_non_refusal(db_session: AsyncSession):
    row = await _row(db_session)
    state = _refusal_state(row, ["phone"])
    assert (
        await flow.maybe_handle_field_refusal(
            db_session, None, state, "9876543210"
        )
        is None
    )
    state2 = _refusal_state(row, [])
    assert (
        await flow.maybe_handle_field_refusal(db_session, None, state2, "No")
        is None
    )
