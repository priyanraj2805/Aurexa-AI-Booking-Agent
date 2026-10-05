"""Placeholder entities must never be masked; repeats must never loop forever.

Production failures:
1. "Please provide a valid email address with an @ symbol, like
   name@example.com." reached WhatsApp as "...like [hidden]." — the entity
   gate treated the illustrative example as invented data. Admin transcript
   (no gate on web) showed the full text.
2. A stuck engine emitted the same reply turn after turn; after the single
   HUMAN nudge the identical body repeated forever ("baar baar ek hi reply").
"""
import pytest

from app.core import whatsapp_client
from app.engine.knowledge_guards import (
    find_unlisted_entities,
    guard_final_text,
)


def test_example_domain_emails_are_never_flagged() -> None:
    reply = (
        "That email has a typo in the domain. Please provide a valid email "
        "address with an @ symbol, like name@example.com."
    )
    assert find_unlisted_entities(reply, [], kinds=("email",)) == []
    safe, blocked, entities = guard_final_text(
        reply, org_facts=[], session_details=[]
    )
    assert blocked is False
    assert entities == []
    assert "name@example.com" in safe
    assert "[hidden]" not in safe


def test_reserved_example_domains_covered() -> None:
    for addr in ("a@example.com", "b@example.org", "c@example.net"):
        assert find_unlisted_entities(f"like {addr}", [], kinds=("email",)) == []


def test_real_unlisted_email_still_masked() -> None:
    """The exemption is narrow: genuinely invented addresses still mask."""
    reply = "You can reach our manager at boss@somecompany.io for help."
    found = find_unlisted_entities(reply, [], kinds=("email",))
    assert found == ["boss@somecompany.io"]
    safe, blocked, _ = guard_final_text(reply, org_facts=[], session_details=[])
    assert blocked is True
    assert "[hidden]" in safe


def test_placeholder_phones_pass_invented_phones_mask() -> None:
    assert (
        find_unlisted_entities("e.g. +1 (555) 000-0000", [], kinds=("phone",))
        == []
    )
    assert find_unlisted_entities("call 0000000000", [], kinds=("phone",)) == []
    # A realistic invented number is still caught.
    assert find_unlisted_entities(
        "call +91 98147 32658", [], kinds=("phone",)
    ) != []


def test_listed_entities_still_pass() -> None:
    reply = "Confirming booking for lulu@6227.com, thanks!"
    assert (
        find_unlisted_entities(
            reply, ["lulu@6227.com"], kinds=("email",)
        )
        == []
    )


def test_phone_mask_is_star_free_and_minimal() -> None:
    """Production: "+916***38" wrapped in **bold** was mangled by WhatsApp
    bold pairing into literal "**" runs, and the pipeline then stripped "*"
    runs so digits joined back. Bullets survive every formatter."""
    from app.engine.user_visible_text import mask_phone_for_display

    masked = mask_phone_for_display("+9163812345")
    assert "*" not in masked
    assert masked.startswith("+91")
    assert masked.endswith("45")
    assert "638123" not in masked
    bare = mask_phone_for_display("8989889898")
    assert "*" not in bare
    assert "988989" not in bare
    assert mask_phone_for_display("") == ""
    assert mask_phone_for_display(None) == ""


def test_masked_cancel_message_survives_outbound_pipeline() -> None:
    """End-to-end shape of the screenshot: bold date + masked phone must
    come out with no literal "**" and the mask intact."""
    from app.core.wa_text import format_outbound
    from app.engine.user_visible_text import mask_phone_for_display

    body = (
        "I found your appointment \u2014 **Thursday, November 05 at 3:00 PM**. "
        "To cancel it, I've sent a verification code to "
        f"**{mask_phone_for_display('+9163812345')}** via WhatsApp. "
        "Please enter the code below to confirm."
    )
    out = format_outbound(body)
    assert "**" not in out
    assert "\u2022" in out  # mask bullets intact
    assert "638123" not in out
    assert "Thursday, November 05" in out


@pytest.mark.asyncio
async def test_third_identical_repeat_is_reframed_not_repeated(db_session):
    """3rd consecutive identical body must differ; the loop must end."""
    import uuid
    from types import SimpleNamespace

    from app.models.session import ChatSession

    row = ChatSession(
        org_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        channel="whatsapp",
        channel_user_id="919000000099",
        state_data={},
        missing_fields=["phone"],
        current_intent="START_BOOKING",
        session_status="ACTIVE",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    sess = SimpleNamespace(
        session_id=str(row.id), collected_data={}, missing_fields=["phone"]
    )
    body = "Please share your phone number."
    first = await whatsapp_client.break_identical_repeat(db_session, sess, body)
    assert first == body
    second = await whatsapp_client.break_identical_repeat(db_session, sess, body)
    assert "HUMAN" in second
    third = await whatsapp_client.break_identical_repeat(db_session, sess, body)
    assert third != body
    assert third != second
    assert "HUMAN" in third
    assert "phone" in third
    fourth = await whatsapp_client.break_identical_repeat(db_session, sess, body)
    assert fourth != third  # rotation: never the same twice running
    fresh = await whatsapp_client.break_identical_repeat(
        db_session, sess, "What date works for you?"
    )
    assert fresh == "What date works for you?"
