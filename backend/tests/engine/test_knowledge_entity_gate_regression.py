"""Regression suite for the knowledge entity gate that broke the booking flow.

Production incident: a customer mid-booking (phone/date/time collected, OTP
pending) sent "Heyyy" and then "Resend otp". The engine produced the correct
OTP prompt and OTP resend in both cases, but the customer received

    "Sorry, I don't have that information in our records right now. ...

instead. The transcript kept the correct text, so the admin inbox and the
customer's phone disagreed.

Cause: the allowed-value pool joined the session fields into ONE string and
then scanned it for digit runs, where the run pattern treats spaces and
dashes as intra-number formatting. "+916350063338" followed by "2026-09-28"
and "3:00 PM" collapsed into the single group "916350063338202609283", so the
customer's own phone number was no longer recognised as theirs and was
reported as an invented entity. The gate then replaced the whole reply.

These tests pin the fix so it cannot come back.
"""
import asyncio

import pytest

from app.core import inbound_context
from app.engine import knowledge_guards as guards


# The exact values from the incident.
OTP_PROMPT = (
    "I've sent a verification code to +916350063338 via WhatsApp. "
    "Please enter it here to continue."
)
OTP_RESEND = (
    "I've sent a new verification code to +916350063338 via WhatsApp. "
    "Please enter it here."
)
ORG_FACTS = ["Priyanshu"]
SESSION = [
    "Priyanshu",
    "priyanshurajthakur114@gmail.com",
    "+916350063338",
    "2026-09-28",
    "3:00 PM",
]


# ── the incident itself ───────────────────────────────────────────────

def test_customer_phone_next_to_date_is_not_an_invention():
    """The bug: phone + date + time must not fuse into one digit group."""
    assert guards.find_unlisted_entities(OTP_PROMPT, [*ORG_FACTS, *SESSION]) == []


def test_digit_groups_never_fuse_across_separate_values():
    groups = set()
    for value in SESSION:
        groups |= guards._digit_groups(value)
    assert "916350063338" in groups
    # The fused blob that caused the incident must not exist.
    assert not any(len(g) > 13 for g in groups), groups


@pytest.mark.parametrize("body", [OTP_PROMPT, OTP_RESEND])
def test_otp_replies_survive_the_entity_gate(body):
    """A legitimate OTP prompt must never be replaced by a decline."""
    safe, blocked, ents = guards.guard_final_text(
        body, org_facts=ORG_FACTS, session_details=SESSION
    )
    assert blocked is False
    assert ents == []
    assert safe == body
    assert "don't have that information" not in safe


@pytest.mark.parametrize(
    "body",
    [
        OTP_PROMPT,
        OTP_RESEND,
        "Your verification code is 123456.",
        "We have sent a new OTP to your phone.",
        "Please enter it here to continue.",
    ],
)
def test_transactional_replies_are_recognised(body):
    assert guards.is_transactional_reply(body) is True


@pytest.mark.parametrize(
    "body",
    [
        "Our clinic is open 9 AM to 6 PM.",
        "We offer hair cuts and colouring.",
        "",
    ],
)
def test_knowledge_answers_are_not_treated_as_transactional(body):
    assert guards.is_transactional_reply(body) is False


def test_knowledge_answer_with_own_phone_still_passes():
    """Non-transactional copy that echoes the customer's phone is fine too."""
    body = "Your appointment is booked for +916350063338. See you then!"
    safe, blocked, _ = guards.guard_final_text(
        body, org_facts=ORG_FACTS, session_details=SESSION
    )
    assert blocked is False
    assert safe == body


# ── the guard must keep doing its real job ────────────────────────────

def test_invented_phone_is_still_blocked():
    """Invented contact data must never reach the customer.

    The reply is NOT discarded and replaced with a canned apology any more —
    that pipeline was removed because it destroyed valid booking messages.
    Only the invented entity is masked; the rest of the sentence survives.
    """
    safe, blocked, ents = guards.guard_final_text(
        "Sure, call Dr. X at +91 11111 22222 to confirm.",
        org_facts=["Acme Clinic"],
        session_details=[],
    )
    assert blocked is True
    assert "+91 11111 22222" in ents
    assert "+91 11111 22222" not in safe
    assert "[hidden]" in safe
    assert "don't have that information" not in safe.lower()


def test_invented_email_is_still_blocked():
    _safe, blocked, ents = guards.guard_final_text(
        "Email us at bogus@nowhere.example",
        org_facts=["Acme Clinic"],
        session_details=SESSION,
    )
    assert blocked is True
    assert "bogus@nowhere.example" in ents


def test_user_own_lookup_value_is_never_masked():
    """Production: "We couldn't find an upcoming appointment for
    **+6350063338**" reached WhatsApp as "...for [hidden]." — the gate
    masked the customer's OWN just-typed lookup value because it sat in
    neither org facts nor session details. Echoes are never inventions."""
    from app.engine.knowledge_guards import guard_final_text

    body = (
        "We couldn't find an upcoming appointment for **+6350063338**. "
        "Please double-check and try again."
    )
    safe, blocked, entities = guard_final_text(
        body, org_facts=[], session_details=[], user_text="63500 63338"
    )
    assert blocked is False
    assert entities == []
    assert "[hidden]" not in safe
    assert "+6350063338" in safe


def test_truly_invented_number_still_masked():
    """The exemption is narrow: a number appearing nowhere (not facts,
    not session, not the user's message) must still mask."""
    from app.engine.knowledge_guards import guard_final_text

    body = "Your manager can be reached at +91 98147 32658 for help."
    safe, blocked, entities = guard_final_text(
        body, org_facts=[], session_details=[], user_text="what are your hours?"
    )
    assert blocked is True
    assert "[hidden]" in safe


def test_known_phone_from_org_facts_passes():
    safe, blocked, _ = guards.guard_final_text(
        "Call us at +91 98765 43210.",
        org_facts=["Reach us on +91 98765 43210 daily."],
        session_details=[],
    )
    assert blocked is False
    assert "98765 43210" in safe


def test_two_numbers_side_by_side_are_handled_symmetrically():
    """Answer and allowed text must tokenize identically, or false blocks."""
    text = "Reach us on 9876543210 9123456780"
    assert guards.find_unlisted_entities(
        f"Call {text}", [text], kinds=("phone",)
    ) == []


# ── inbound id must reach every persistence call ──────────────────────

def test_inbound_id_is_scoped_and_restored():
    inbound_context.set_inbound_message_id(None)
    with inbound_context.inbound_message("wamid.AAA"):
        assert inbound_context.get_inbound_message_id() == "wamid.AAA"
        with inbound_context.inbound_message("wamid.BBB"):
            assert inbound_context.get_inbound_message_id() == "wamid.BBB"
        assert inbound_context.get_inbound_message_id() == "wamid.AAA"
    assert inbound_context.get_inbound_message_id() is None


def test_inbound_id_does_not_leak_between_concurrent_messages():
    """Two customers handled at once must never see each other's id."""

    async def handle(msg_id):
        with inbound_context.inbound_message(msg_id):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return inbound_context.get_inbound_message_id()

    async def run():
        return await asyncio.gather(handle("wamid.ONE"), handle("wamid.TWO"))

    assert asyncio.run(run()) == ["wamid.ONE", "wamid.TWO"]


def test_blank_inbound_id_is_normalised_to_none():
    with inbound_context.inbound_message("   "):
        assert inbound_context.get_inbound_message_id() is None


@pytest.mark.asyncio
async def test_save_message_picks_up_ambient_inbound_id(db_session):
    """Helper flows that pass no id must still record the provider id."""
    from app.crud import chat as crud_chat
    from app.models.message import ChatMessage
    from sqlalchemy.future import select as _sel

    with inbound_context.inbound_message("wamid.AMBIENT"):
        row = await crud_chat.save_message(
            db_session, str(__import__("uuid").uuid4()), "user", "Resend otp"
        )
    await db_session.commit()

    fetched = (
        await db_session.execute(_sel(ChatMessage).where(ChatMessage.id == row.id))
    ).scalar_one()
    assert fetched.external_message_id == "wamid.AMBIENT"


@pytest.mark.asyncio
async def test_ambient_inbound_id_marks_user_rows_only(db_session):
    """The bot's reply must NOT inherit the inbound message's id.

    external_message_id means "the provider message that produced this row".
    A user turn carries the inbound wamid; the agent row that answered it
    carries its own outbound id in provider_message_id. Stamping both with
    the inbound id made a user row and its reply share one identifier.
    """
    from app.crud import chat as crud_chat
    from app.models.message import ChatMessage
    from sqlalchemy.future import select as _sel
    import uuid as _uuid

    session_id = str(_uuid.uuid4())
    with inbound_context.inbound_message("wamid.INBOUND1"):
        user_row = await crud_chat.save_message(
            db_session, session_id, "user", "Hello"
        )
        agent_row = await crud_chat.save_message(
            db_session, session_id, "agent", "Hello! How can I help?"
        )
    await db_session.commit()

    fetched = (
        await db_session.execute(
            _sel(ChatMessage).where(ChatMessage.id.in_([user_row.id, agent_row.id]))
        )
    ).scalars().all()
    by_id = {r.id: r for r in fetched}

    assert by_id[user_row.id].external_message_id == "wamid.INBOUND1"
    assert by_id[agent_row.id].external_message_id is None


@pytest.mark.asyncio
async def test_explicit_inbound_id_wins_over_ambient(db_session):
    from app.crud import chat as crud_chat
    from app.models.message import ChatMessage
    from sqlalchemy.future import select as _sel

    with inbound_context.inbound_message("wamid.AMBIENT"):
        row = await crud_chat.save_message(
            db_session,
            str(__import__("uuid").uuid4()),
            "user",
            "Heyyy",
            external_message_id="wamid.EXPLICIT",
        )
    await db_session.commit()

    fetched = (
        await db_session.execute(_sel(ChatMessage).where(ChatMessage.id == row.id))
    ).scalar_one()
    assert fetched.external_message_id == "wamid.EXPLICIT"
