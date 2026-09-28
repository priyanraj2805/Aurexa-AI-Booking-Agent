"""Regression suite for the state/message-association root causes.

Proven defects this file locks down:

1. ``is_confirm_message`` used substring matching and included ``"k"`` in its
   accept set, so ordinary sentences containing the letter k ("I want to know
   about dotstark", "okayy", "book an appointment for me") were treated as
   booking confirmations. Confirmation matching must be whole-word only.
2. Directive-tap handling rewrote the inbound text to the option value before
   the engine ran, so the transcript lost what the human actually sent. The
   engine must accept the original text for the transcript while still driving
   booking logic from the rewritten text.
3. Inbound provider message IDs were never persisted, so a transcript row could
   not be traced back to the exact provider message.
4. QR and Meta Cloud derived different session-lock keys, so one conversation
   could be processed under two independent locks.
5. A stuck cancel lookup kept intercepting unrelated turns and re-asking for a
   contact, looping the conversation.

All tests run offline on sqlite / pure functions.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.future import select as _sel

from app.core.whatsapp_debouncer import session_lock_key
from app.crud import chat as crud_chat
from app.engine import message_processor
from app.engine.cancel_flow import is_cancel_exit_request, is_cancel_topic_change
from app.engine.time_flex import is_confirm_message, is_pure_confirm_message
from app.models.config import OrgConfig
from app.models.message import ChatMessage
from app.models.organization import Organization
from app.schemas.agent import ActionPayload


# ── 1. confirm matcher must not match arbitrary k-containing text ──────

@pytest.mark.parametrize(
    "text",
    [
        "I want to know about dotstark",
        "okayy",
        "k",
        "kk",
        "book an appointment for me",
        "thanks",
        "I booked a slot yesterday",
        "do you know the price?",
        "kindly share the timing",
        "make it a cake",
        "okay then",
        "no booking available",
    ],
)
def test_k_containing_text_is_not_a_confirmation(text):
    """A sentence is never a confirmation just because it contains 'k'."""
    assert is_confirm_message(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "confirm",
        "confirm booking",
        "confirm my booking",
        "ok",
        "okay",
        "sure",
        "go ahead",
        "book it",
        "Comfirm",
        "CONFIRM",
        "  yes  ",
    ],
)
def test_real_confirmations_still_match(text):
    """Whole-word matching must not regress genuine confirmations."""
    assert is_confirm_message(text) is True


def test_pure_confirm_never_matches_sentences():
    """The strict post-booking matcher stays an exact allow-list.

    Typo tolerance lives only in the fuzzy matcher, so a typo that the loose
    matcher accepts must still be rejected here.
    """
    assert is_pure_confirm_message("I want to know about dotstark") is False
    assert is_pure_confirm_message("okay") is True
    assert is_pure_confirm_message("Comfirm") is False
    assert is_pure_confirm_message("okayy") is False
    assert is_pure_confirm_message("okay then") is False


@pytest.mark.parametrize(
    "text",
    [
        "okay then",
        "yes but what about tomorrow?",
        "ok so",
        "confirm then",
        "sure?",
    ],
)
def test_unfinished_or_questioned_utterances_are_not_confirmations(text):
    """A trailing conjunction or a question mark means the thought continues."""
    assert is_confirm_message(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "yes please",
        "confirm booking",
        "go ahead",
        "okay, confirm it",
        "yes!",
    ],
)
def test_confirmations_with_trailing_filler_still_match(text):
    """The unfinished-utterance guard must not eat valid confirmations."""
    assert is_confirm_message(text) is True


# ── 4. one canonical lock key per conversation ─────────────────────────

def test_session_lock_key_is_transport_independent():
    """QR and Cloud must never end up with two independent locks."""
    session_id = "5f2c9a1e-0b3d-4a77-9c11-8e2f6d0b4a55"
    assert session_lock_key(session_id) == session_lock_key(f"cloud:{session_id}")
    assert session_lock_key(session_id) == session_lock_key(f"meta:{session_id}")
    assert session_lock_key(session_id) == session_lock_key(f"qr:{session_id}")


def test_session_lock_key_distinguishes_real_sessions():
    assert session_lock_key("session-a") != session_lock_key("session-b")
    assert session_lock_key("") != session_lock_key("session-a")


def test_get_session_lock_returns_same_lock_for_both_transports():
    """The actual lock object must be shared, not just the key string."""
    from app.core.whatsapp_debouncer import get_session_lock

    session_id = f"lock-{uuid.uuid4().hex}"
    assert get_session_lock(session_id) is get_session_lock(f"cloud:{session_id}")


# ── 5. cancel lookup must stand down on a topic change ────────────────

@pytest.mark.parametrize(
    "text",
    [
        "do you offer hair cuts?",
        "what is the price?",
        "what slots do you have today?",
        "reschedule my appointment",
        "what time do you close?",
    ],
)
def test_topic_change_releases_cancel_lookup(text):
    assert is_cancel_topic_change(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "my email is test@example.com",
        "test@example.com",
        "9876543210",
        "cancel my booking",
        "find my booking",
        "continue",
        "keep going",
        "my phone is 9876543210",
        "as I said",
        "",
    ],
)
def test_in_flow_answers_are_not_topic_changes(text):
    """A genuine contact answer must never release the lookup mid-flight."""
    assert is_cancel_topic_change(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "I want to book a new appointment",
        "book me a slot for tomorrow",
        "never mind",
    ],
)
def test_new_booking_wording_is_caught_by_the_exit_gate(text):
    """Explicit new-booking wording exits via is_cancel_exit_request, which
    runs before the topic-change escape, so it must stay classified there."""
    assert is_cancel_exit_request(text) is True


@pytest.mark.asyncio
async def test_otp_redaction_outranks_transcript_override():
    """A pasted OTP must never land in the transcript in the clear.

    Privacy redaction wins over the original-text override: the inbound
    handler always supplies the real text, so honoring it blindly would put
    OTP codes into chat_messages and Admin Inbox.
    """
    from unittest.mock import patch

    from langchain_core.messages import AIMessage

    from app.engine import langgraph_orchestrator
    from app.schemas.agent import ChatSessionState

    async def _ainvoke(inputs, config=None):
        return {
            "messages": [AIMessage(content="ok")],
            "collected_fields": {},
            "follow_up_fields": ["name"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "START_BOOKING",
        }

    chat_session = ChatSessionState(
        session_id=f"sess-redact-{uuid.uuid4().hex[:8]}",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="whatsapp",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "email", "type": "email", "label": "Email",
                 "required": True, "order": 0}
            ],
            "email": "a@b.com",
        },
        missing_fields=[],
        current_intent="PROVIDE_DATA",
        otp_verified=False,
    )
    org_config = SimpleNamespace(timezone="UTC", require_otp=True, require_phone_otp=False)

    with patch.object(langgraph_orchestrator, "_is_awaiting_otp", return_value=True), \
         patch.object(langgraph_orchestrator, "_active_effect_state",
                      return_value={"otp_sent_keys": ["otp:test"]}), \
         patch.object(langgraph_orchestrator, "resolve_otp_verify_channels",
                      return_value=["email"]), \
         patch.object(langgraph_orchestrator, "_persist_transcript_best_effort",
                      new=AsyncMock()) as mock_persist, \
         patch.object(langgraph_orchestrator.crud_chat, "update_chat_session",
                      new=AsyncMock()), \
         patch.object(langgraph_orchestrator.crud_chat, "save_session_checkpoint",
                      new=AsyncMock()), \
         patch("app.crud.otp.verify_otp", new=AsyncMock(return_value=False)), \
         patch("app.crud.otp.get_otp_policy_metadata",
               new=AsyncMock(return_value={"cooldown_remaining_seconds": 0})), \
         patch.object(langgraph_orchestrator.booking_agent, "ainvoke", _ainvoke):
        await langgraph_orchestrator.process_message_with_graph(
            db=SimpleNamespace(execute=AsyncMock()),
            org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
            org_config=org_config,
            chat_session=chat_session,
            message_text="123456",
            transcript_user_text="123456",
            inbound_message_id="wamid.OTP123",
        )

    assert mock_persist.await_count == 1
    persisted = mock_persist.call_args.kwargs["user_message"]
    assert persisted == "[verification code entered]"
    assert "123456" not in persisted


# ── helpers for transcript persistence tests ──────────────────────────

async def _make_org(db_session, **cfg_kwargs):
    org = Organization(name=f"StateMsg Org {uuid.uuid4().hex[:6]}", api_key=f"ds_{uuid.uuid4().hex}")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    cfg = {
        "required_fields": ["name", "email", "phone", "date", "time"],
        "require_otp": False,
        "require_phone_otp": False,
        "timezone": "UTC",
    }
    cfg.update(cfg_kwargs)
    config = OrgConfig(org_id=org.id, **cfg)
    db_session.add(config)
    await db_session.commit()
    return org, config


async def _make_session(db_session, org, channel="whatsapp", status="CONFIRMING",
                        intent="CONFIRM_BOOKING", collected=None):
    created = await crud_chat.create_chat_session(
        db_session, str(org.id), channel=channel,
        channel_user_id=f"wa_{uuid.uuid4().hex[:10]}",
    )
    sid = str(created.id)
    sess = await crud_chat.get_chat_session(db_session, sid)
    sess.session_status = status
    sess.current_intent = intent
    sess.otp_verified = True
    if collected is not None:
        sess.collected_data = dict(collected)
    await crud_chat.update_chat_session(db_session, sid, sess)
    final = await crud_chat.get_chat_session(db_session, sid)
    return final


# ── 2 + 3. transcript keeps original text and inbound provider id ──────

@pytest.mark.asyncio
async def test_transcript_stores_original_text_when_caller_rewrote_it(
    db_session, monkeypatch
):
    """Engine writes what the human sent, not the rewritten directive value.

    Booking logic must still see the rewritten text (that is what completes the
    step), but the persisted user row must be the original inbound message.
    """
    org, cfg = await _make_org(db_session)
    sess = await _make_session(
        db_session, org,
        collected={"name": "Asha", "email": "a@b.com", "phone": "919000000001",
                   "date": "2026-10-10", "time": "10:00 AM"},
    )

    captured = []

    async def _fake_save(db, session_id, role, content, **kwargs):
        captured.append((role, content, kwargs))

    monkeypatch.setattr(message_processor.crud_chat, "save_message", _fake_save)

    action = await message_processor.process_message(
        db_session,
        org,
        cfg,
        sess,
        message_text="confirm",
        transcript_user_text="Comfirm my booking plz",
        inbound_message_id="BAEY1234567890",
    )

    assert isinstance(action, ActionPayload)
    user_rows = [r for r in captured if r[0] == "user"]
    assert user_rows, "expected the turn to be persisted"
    for _role, content, _kwargs in user_rows:
        assert content == "Comfirm my booking plz"


@pytest.mark.asyncio
async def test_transcript_defaults_to_processed_text_without_override(
    db_session, monkeypatch
):
    """Without an override the persisted text is the processed text."""
    org, cfg = await _make_org(db_session)
    sess = await _make_session(
        db_session, org, status="ACTIVE", intent="START_BOOKING", collected={},
    )

    captured = []

    async def _fake_save(db, session_id, role, content, **kwargs):
        captured.append((role, content, kwargs))

    monkeypatch.setattr(message_processor.crud_chat, "save_message", _fake_save)

    await message_processor.process_message(
        db_session, org, cfg, sess, message_text="thanks",
    )

    user_rows = [r for r in captured if r[0] == "user"]
    assert user_rows
    for _role, content, _kwargs in user_rows:
        assert content == "thanks"


@pytest.mark.asyncio
async def test_save_message_persists_external_message_id(db_session):
    """crud.save_message must write the inbound provider id onto the row."""
    session_id = str(uuid.uuid4())

    row = await crud_chat.save_message(
        db_session,
        session_id,
        "user",
        "do you know the price?",
        external_message_id="wamid.HBgNNTUxMjM",
    )
    await db_session.commit()

    fetched = (
        await db_session.execute(
            _sel(ChatMessage).where(ChatMessage.id == row.id)
        )
    ).scalar_one()
    assert fetched.external_message_id == "wamid.HBgNNTUxMjM"
    assert fetched.content == "do you know the price?"


@pytest.mark.asyncio
async def test_save_message_allows_missing_external_id(db_session):
    """Non-WhatsApp channels have no provider id; the column must stay null."""
    row = await crud_chat.save_message(
        db_session, str(uuid.uuid4()), "user", "hello from web",
    )
    await db_session.commit()
    assert row.external_message_id is None
