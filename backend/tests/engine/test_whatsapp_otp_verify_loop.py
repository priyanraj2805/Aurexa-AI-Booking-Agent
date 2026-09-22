"""Regression: a correct WhatsApp (phone-channel) OTP must verify once and
proceed — never loop back to "enter the code again".

Root cause fixed alongside this test: verify_otp() records the
verified-context key straight into the DB row, while the turn keeps a
stale in-memory snapshot. _handle_booking_flow's context check then saw
a verified flag with a missing key, revoked the verification, generated
a new code and re-asked — forever. Both OTP fast-paths now reload the
session row right after a successful verify so the key is present.
"""
import uuid

import pytest

from app.crud import chat as crud_chat
from app.crud import otp as otp_crud
from app.engine import message_processor
from app.models.config import OrgConfig
from app.models.organization import Organization


@pytest.mark.asyncio
async def test_phone_verify_key_survives_reload_and_matches_context(db_session):
    org = Organization(name="OTP Loop Org", api_key=f"ds_{uuid.uuid4().hex}")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    db_session.add(
        OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
        )
    )
    await db_session.commit()

    created = await crud_chat.create_chat_session(
        db_session, str(org.id), channel="whatsapp",
        channel_user_id="916350063338",
    )
    sid = str(created.id)
    created.collected_data = {
        "phone": "916350063338",
        "date": "2026-09-17",
        "time": "11:00 AM",
    }
    await crud_chat.update_chat_session(db_session, sid, created)

    # Snapshot as the inbound turn holds it (predates verify).
    stale = await crud_chat.get_chat_session(db_session, sid)
    assert stale is not None

    code = await otp_crud.generate_otp(db_session, sid, channel="phone", resend=False)
    assert await otp_crud.verify_otp(db_session, sid, code, channel="phone") is True

    # The stale snapshot cannot see the just-written verified key...
    stale_internal = (stale.collected_data.get("_internal") or {})
    assert "phone_otp_verified_key" not in stale_internal

    # ...so callers must reload (as the fixed fast-paths do): the reloaded
    # session carries both the flag and the key, and the key must equal the
    # context key _handle_booking_flow will compare against.
    fresh = await crud_chat.get_chat_session(db_session, sid)
    assert fresh is not None
    assert fresh.phone_otp_verified is True
    fresh_key = (fresh.collected_data.get("_internal") or {}).get(
        "phone_otp_verified_key"
    )
    assert fresh_key == message_processor._build_phone_otp_context_key(fresh)

    # Persisting the reloaded snapshot keeps the key (no silent revoke).
    await crud_chat.update_chat_session(db_session, sid, fresh)
    refetched = await crud_chat.get_chat_session(db_session, sid)
    assert refetched is not None
    assert refetched.phone_otp_verified is True
    assert (refetched.collected_data.get("_internal") or {}).get(
        "phone_otp_verified_key"
    ) == fresh_key
