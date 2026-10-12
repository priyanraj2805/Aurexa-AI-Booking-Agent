"""WhatsApp hallucination stress suite.

Fires adversarial / ambiguous / edge-case user messages at the booking
engine and asserts GROUNDEDNESS properties instead of exact copy:

  * no confirmation claim without a persisted Booking row (C1)
  * no BOOKING_CONFIRMED_CARD unless session_status == CONFIRMED
  * one active booking per email/phone (duplicate guard)
  * finished sessions never re-enter booking/OTP flows (OTP-reuse)
  * LLM verdict wins over keyword fallbacks; fallbacks still work
  * validators reject malformed step input deterministically
  * reply formatter never leaks markdown/debris

All tests run on sqlite + mocked LLM (no network). Each test uses a
unique org/contact so rows never leak across cases.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.future import select as _sel

from app.crud import chat as crud_chat
from app.engine import message_processor
from app.engine.user_visible_text import (
    looks_like_confirmation_claim,
    looks_like_field_saved_claim,
    looks_like_json_debris,
)
from app.models.booking import Booking, TimeSlot
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.schemas.agent import ActionPayload, parse_field_definitions


# ── helpers ──────────────────────────────────────────────────────────

async def _make_org(db_session, **cfg_kwargs):
    org = Organization(name=f"Stress Org {uuid.uuid4().hex[:6]}", api_key=f"ds_{uuid.uuid4().hex}")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    cfg = {"required_fields": ["name", "email", "phone", "date", "time"],
           "require_otp": False, "require_phone_otp": False, "timezone": "UTC"}
    cfg.update(cfg_kwargs)
    db_session.add(OrgConfig(org_id=org.id, **cfg))
    await db_session.commit()
    return org


async def _make_session(db_session, org, channel="whatsapp", user="wa_9000000001",
                        status="ACTIVE", intent="START_BOOKING", missing=None,
                        collected=None):
    created = await crud_chat.create_chat_session(
        db_session, str(org.id), channel=channel, channel_user_id=user)
    sid = str(created.id)
    sess = await crud_chat.get_chat_session(db_session, sid)
    assert sess is not None
    sess.session_status = status
    sess.current_intent = intent
    sess.missing_fields = list(missing) if missing is not None else list(created.missing_fields or [])
    if collected is not None:
        sess.collected_data = dict(collected)
    await crud_chat.update_chat_session(db_session, sid, sess)
    final = await crud_chat.get_chat_session(db_session, sid)
    assert final is not None
    return final


def _fd_map():
    return {fd.name: fd for fd in parse_field_definitions(["name", "email", "phone", "date", "time"])}


def _mock_llm_intent(monkeypatch, intent, entities=None):
    async def _fake_turn(*a, **k):
        return {"intent": intent, "extracted_entities": dict(entities or {}),
                "response_message": "mocked"}
    monkeypatch.setattr(message_processor.llm_engine, "process_turn", _fake_turn)


async def _booking_count(db_session, org_id):
    """Bookings for ONE org only — sqlite is shared across tests in a run."""
    return len((await db_session.execute(
        _sel(Booking).where(Booking.org_id == org_id))).scalars().all())


def _fail_commit(monkeypatch):
    """Simulate the C1 race: slot taken between lock and commit."""
    import app.crud.booking as _booking_crud
    monkeypatch.setattr(
        _booking_crud, "attempt_booking",
        AsyncMock(side_effect=ValueError("Slot is no longer available")))


FULL_CONTACT = {"name": "Stress User", "email": "s1@example.com",
                "phone": "919000000001", "date": "2026-10-10", "time": "10:00 AM"}


# ── G. detector calibration ──────────────────────────────────────────

class TestDetectors:
    def test_confirmation_claim_true_positive(self):
        assert looks_like_confirmation_claim("Your appointment is booked! 🎉") is True

    def test_confirmation_question_not_flagged(self):
        assert looks_like_confirmation_claim(
            "Please confirm your booking: 10 Oct at 10:00 AM. Reply CONFIRM or CHANGE.") is False

    def test_confirmation_negation_not_flagged(self):
        assert looks_like_confirmation_claim("Your slot is not booked yet.") is False

    def test_json_debris_detected(self):
        assert looks_like_json_debris('{"response_text": "hi", "extracted_fields": {}}') is True
        assert looks_like_json_debris("Hello! How can I help you today?") is False

    def test_field_saved_claim(self):
        assert looks_like_field_saved_claim("I've saved your phone number.", "phone") is True
        assert looks_like_field_saved_claim("What is your phone number?", "phone") is False


# ── E. step-guard validators ─────────────────────────────────────────

class TestStepGuards:
    def test_bare_number_is_never_a_date(self):
        from app.engine.whatsapp_guards import validate_typed_date
        iso, correction, reason = validate_typed_date(
            "2", tz="UTC", max_days=30, examples=["17/09/2026", "18/09/2026"])
        assert iso is None and reason == "bare_number" and "17/09/2026" in (correction or "")

    def test_gibberish_date_rejected(self):
        from app.engine.whatsapp_guards import validate_typed_date
        iso, correction, reason = validate_typed_date(
            "32/13/2026", tz="UTC", max_days=30, examples=["17/09/2026"])
        assert iso is None and reason == "invalid"

    def test_slot_index_only_for_slots(self):
        from app.engine.whatsapp_guards import match_slot_index, match_option_by_text
        opts = [{"id": f"s{i}", "label": f"{9 + i}:00 AM", "value": f"{9 + i}:00 AM"} for i in range(3)]
        assert match_slot_index(opts, "2") == opts[1]
        assert match_slot_index(opts, "9") is None
        assert match_option_by_text(opts, "10:00 AM") == opts[1]
        assert match_option_by_text(opts, "2") is None  # no positional date-pick

    def test_objection_detected_with_field(self):
        from app.engine.whatsapp_guards import detect_objection
        hit = detect_objection("why do you need my email?", {"name": "A"}, "email")
        assert hit and hit["category"] == "why_needed" and hit["field"] == "email"

    def test_confirm_typo_tolerated(self):
        from app.engine.time_flex import is_confirm_message
        assert is_confirm_message("Comfirm") is True
        assert is_confirm_message("yes please") is True
        assert is_confirm_message("what are the charges?") is False

    def test_cancel_question_not_trapped(self):
        from app.engine.whatsapp_guards import is_cancel_bypass
        assert is_cancel_bypass("cancel my booking") is True
        # "cancellation" must NOT match \bcancel\b — info questions stay free
        assert is_cancel_bypass("what is the cancellation policy?") is False


# ── F. reply formatter ───────────────────────────────────────────────

class TestReplyFormatter:
    @staticmethod
    def _payload(directive, message="Hello", meta=None):
        return SimpleNamespace(widget_directive=directive, message=message,
                               field_meta=meta or {})

    def test_every_directive_renders_clean_text(self):
        from app.core.whatsapp_client import format_action_for_whatsapp
        cases = [
            ("TEXT_INPUT", "Hi there", {}),
            ("REQUIRE_DATE_PICKER", "Pick a date", {"options": ["2026-10-10"]}),
            ("REQUIRE_TIME_SLOT_GRID", "Pick a time", {"options": ["10:00 AM"]}),
            ("REQUIRE_OTP_MODAL", "Enter code", {}),
            ("BOOKING_CONFIRMED_CARD", "Booked!", {}),
            ("REQUEST_CONFIRMATION", "Confirm?", {}),
            ("REQUIRE_SELECT", "Choose", {"options": ["a", "b"]}),
        ]
        for directive, msg, meta in cases:
            out = format_action_for_whatsapp(self._payload(directive, msg, meta))
            assert out.get("body"), directive
            assert "**" not in out["body"], directive
            assert not looks_like_json_debris(out["body"]), directive

    def test_otp_modal_appends_code_instruction(self):
        from app.core.whatsapp_client import format_action_for_whatsapp
        out = format_action_for_whatsapp(self._payload("REQUIRE_OTP_MODAL", "Code sent"))
        assert "verification code" in out["body"].lower()


# ── A. no false confirmations (C1) ───────────────────────────────────

class TestNoFalseConfirmations:
    async def test_yes_with_no_slot_never_confirms(self, db_session, monkeypatch):
        """Commit fails (slot taken at commit = the C1 race): reply must not
        claim a booking and no Booking row may exist."""
        org = await _make_org(db_session)
        sess = await _make_session(
            db_session, org, missing=[], intent="CONFIRM_BOOKING",
            collected=dict(FULL_CONTACT, email="a1_noslot@example.com"))
        _mock_llm_intent(monkeypatch, "PROVIDE_DATA")
        _fail_commit(monkeypatch)
        action = await message_processor.process_message(
            db_session, org,
            (await db_session.execute(
                _sel(OrgConfig).where(
                    OrgConfig.org_id == org.id))).scalars().first(),
            sess, "yes")
        assert isinstance(action, ActionPayload)
        assert action.widget_directive != "BOOKING_CONFIRMED_CARD"
        assert looks_like_confirmation_claim(action.message or "") is False
        assert await _booking_count(db_session, org.id) == 0

    async def test_graph_fallback_never_confirms_without_row(self, db_session, monkeypatch):
        """Full WhatsApp entry (route_whatsapp_message) with dead graph AND
        failed commit: graph raises -> legacy fallback -> still grounded."""
        from app.engine import runtime_router
        from app.engine.agent import graph as graph_mod
        org = await _make_org(db_session)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(
            db_session, org, missing=[], intent="CONFIRM_BOOKING",
            collected=dict(FULL_CONTACT, email="a2_fallback@example.com"))
        monkeypatch.setattr(graph_mod.booking_agent, "ainvoke",
                            AsyncMock(side_effect=RuntimeError("llm down")))
        _mock_llm_intent(monkeypatch, "PROVIDE_DATA")
        _fail_commit(monkeypatch)
        action = await runtime_router.route_whatsapp_message(
            db_session, org, cfg, sess, "yes")
        assert action.widget_directive != "BOOKING_CONFIRMED_CARD"
        assert looks_like_confirmation_claim(action.message or "") is False
        assert await _booking_count(db_session, org.id) == 0


# ── B. single active booking ─────────────────────────────────────────

class TestSingleActiveBooking:
    async def _seed_upcoming(self, db_session, org, email, days_ahead=2):
        start = datetime.now(timezone.utc) + timedelta(days=days_ahead)
        slot = TimeSlot(org_id=org.id, start_time=start,
                        end_time=start + timedelta(hours=1), is_booked=True)
        db_session.add(slot)
        await db_session.commit()
        await db_session.refresh(slot)
        db_session.add(Booking(org_id=org.id, slot_id=slot.id, session_id=None,
                               customer_name="Seed", customer_email=email,
                               customer_phone="919000000099"))
        await db_session.commit()

    async def test_duplicate_email_blocked_with_details(self, db_session):
        org = await _make_org(db_session)
        await self._seed_upcoming(db_session, org, "dup_block@example.com")
        sess = await _make_session(
            db_session, org, missing=[], intent="CONFIRM_BOOKING",
            collected=dict(
                FULL_CONTACT,
                email="dup_block@example.com",
                _review_presented=True,
            ))
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        msg, directive, meta = await message_processor._handle_booking_flow(
            db_session, org, cfg, sess, "CONFIRM_BOOKING", False, _fd_map())
        assert directive == "TEXT_INPUT"
        assert (meta or {}).get("duplicate_booking") is True
        assert "cancel my booking" in msg.lower()
        assert "dup_block@example.com" in msg
        assert await _booking_count(db_session, org.id) == 1  # no second row

    async def test_fresh_email_passes_guard(self, db_session):
        org = await _make_org(db_session)
        await self._seed_upcoming(db_session, org, "other@example.com")
        sess = await _make_session(
            db_session, org, missing=[], intent="START_BOOKING",
            collected=dict(FULL_CONTACT, email="fresh_new@example.com"))
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        msg, directive, meta = await message_processor._handle_booking_flow(
            db_session, org, cfg, sess, "START_BOOKING", False, _fd_map())
        assert (meta or {}).get("duplicate_booking") is not True

    async def test_past_booking_does_not_block(self, db_session):
        org = await _make_org(db_session)
        start = datetime.now(timezone.utc) - timedelta(days=2)
        slot = TimeSlot(org_id=org.id, start_time=start,
                        end_time=start + timedelta(hours=1), is_booked=True)
        db_session.add(slot)
        await db_session.commit()
        await db_session.refresh(slot)
        db_session.add(Booking(org_id=org.id, slot_id=slot.id, session_id=None,
                               customer_name="Old", customer_email="past_only@example.com",
                               customer_phone="919000000098"))
        await db_session.commit()
        sess = await _make_session(
            db_session, org, missing=[], intent="CONFIRM_BOOKING",
            collected=dict(FULL_CONTACT, email="past_only@example.com"))
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        msg, directive, meta = await message_processor._handle_booking_flow(
            db_session, org, cfg, sess, "CONFIRM_BOOKING", False, _fd_map())
        assert (meta or {}).get("duplicate_booking") is not True


# ── C. finished sessions stay finished (OTP-reuse) ───────────────────

class TestFinishedSessionsStayFinished:
    @staticmethod
    def _mock_post_booking(monkeypatch, **kw):
        from app.engine import langgraph_orchestrator as _lo
        base = {"classification": "NEUTRAL", "action": "NONE",
                "confidence": 0.0, "reply": "Noted."}
        base.update(kw)
        monkeypatch.setattr(_lo, "_llm_post_booking_analysis",
                            AsyncMock(return_value=base))

    async def test_resent_otp_on_confirmed_never_rebooks(self, db_session, monkeypatch):
        from app.engine import langgraph_orchestrator
        org = await _make_org(db_session)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(
            db_session, org, status="CONFIRMED", intent="CONFIRM_BOOKING",
            missing=[], collected=dict(FULL_CONTACT))
        self._mock_post_booking(monkeypatch)
        # the exact reported repro: user re-sends the old 6-digit code
        action = await langgraph_orchestrator.process_message_with_graph(
            db_session, org, cfg, sess, "482916")
        assert action.widget_directive == "TEXT_INPUT"
        assert action.widget_directive != "REQUIRE_OTP_MODAL"
        assert looks_like_confirmation_claim(action.message or "") is False
        assert await _booking_count(db_session, org.id) == 0

    async def test_gibberish_on_expired_never_books(self, db_session, monkeypatch):
        from app.engine import langgraph_orchestrator
        org = await _make_org(db_session)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(
            db_session, org, status="EXPIRED", intent="START_BOOKING",
            collected=dict(FULL_CONTACT))
        self._mock_post_booking(monkeypatch)
        for weird in ["482916", "???", "asdfgh", "👍👍👍"]:
            action = await langgraph_orchestrator.process_message_with_graph(
                db_session, org, cfg, sess, weird)
            assert action.widget_directive == "TEXT_INPUT", weird
        assert await _booking_count(db_session, org.id) == 0

    async def test_explicit_rebook_after_cancel_still_works(self, db_session, monkeypatch):
        from app.engine import langgraph_orchestrator
        org = await _make_org(db_session)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(
            db_session, org, status="CANCELLED", intent="START_BOOKING",
            collected=dict(FULL_CONTACT))
        self._mock_post_booking(
            monkeypatch, classification="ACTION_REQUEST", action="REBOOK",
            confidence=0.95, reply="")
        action = await langgraph_orchestrator.process_message_with_graph(
            db_session, org, cfg, sess, "i want to book again")
        assert "full name" in (action.message or "").lower()
        assert await _booking_count(db_session, org.id) == 0


# ── J. constant-answer loops ───────────────────────────────────────

class TestLoopBusting:
    @pytest.mark.parametrize("word", [
        "no", "No", "NO", "noo", "nope", "Nopee", "NOPEEE",
        "nah", "Nahh", "NAHHH", "no thanks", "No Thanks",
    ])
    def test_standalone_no_is_decline(self, word):
        from app.engine.decline_flow import is_no_booking_request
        assert is_no_booking_request(word) is True

    @pytest.mark.parametrize("text", [
        "nope, tell me your timings",
        "no, is parking free?",
        "don't want to cancel",
        "do not cancel my booking",
        "note this down",
        "number please",
    ])
    def test_standalone_no_does_not_overreach(self, text):
        from app.engine.decline_flow import is_no_booking_request
        assert is_no_booking_request(text) is False

    async def test_decline_transcript_keeps_user_words(self, db_session):
        from app.engine import decline_flow
        org = await _make_org(db_session)
        sess = await _make_session(db_session, org, user="wa_9100000091")
        action = await decline_flow.start_decline(
            db_session, org, sess, "Nopee")
        assert "no problem" in (action.message or "").lower()
        msgs = await crud_chat.get_session_messages(db_session, sess.session_id)
        user_rows = [m for m in msgs if m.role == "user"]
        assert user_rows and user_rows[-1].content == "Nopee"

    async def test_empty_input_stays_silent(self, db_session):
        from app.engine import langgraph_orchestrator
        org = await _make_org(db_session)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(db_session, org, user="wa_9100000092")
        for empty in ["", "   ", "\n\t "]:
            for fn in (message_processor.process_message,
                       langgraph_orchestrator.process_message_with_graph):
                action = await fn(db_session, org, cfg, sess, empty)
                assert action.widget_directive == "SILENT_NO_REPLY"
        msgs = await crud_chat.get_session_messages(db_session, sess.session_id)
        assert msgs == []

    async def test_identical_repeat_never_mutates_text(self, db_session):
        from app.core import whatsapp_client
        org = await _make_org(db_session)
        sess = await _make_session(db_session, org, user="wa_9100000093")
        body = "Please share your full name."
        # Observe-only: repeats are tracked for diagnostics, but the text
        # is NEVER appended to — no HUMAN/nudge lines reach the customer.
        for _ in range(4):
            out = await whatsapp_client.break_identical_repeat(db_session, sess, body)
            assert out == body
            assert "HUMAN" not in out
        fresh = await whatsapp_client.break_identical_repeat(
            db_session, sess, "What date works for you?")
        assert fresh == "What date works for you?"  # progress resets

    async def test_nopee_exits_name_interrogation(self, db_session, monkeypatch):
        """The exact live loop: name-missing + 'Nopee' must decline, not
        re-ask for the name."""
        org = await _make_org(db_session)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(
            db_session, org, missing=["name", "email", "phone", "date", "time"],
            intent="PROVIDE_DATA", collected={}, user="wa_9100000094")
        _mock_llm_intent(monkeypatch, "PROVIDE_DATA")
        action = await message_processor.process_message(
            db_session, org, cfg, sess, "Nopee")
        assert "no problem" in (action.message or "").lower()
        refetched = await crud_chat.get_chat_session(db_session, sess.session_id)
        assert refetched is not None
        assert (refetched.collected_data or {}).get("_booking_declined") is True


# ── H. conversational brain (chitchat) ─────────────────────────────

class TestChitchatBrain:
    @pytest.mark.parametrize("word", [
        "thanks", "Thank you", "hello", "hi", "bye", "good morning",
        "good evening", "hey", "how are you",
    ])
    def test_chitchat_never_validates_as_name(self, word):
        from app.engine.validators import DataValidator
        cleaned, err = DataValidator._validate_name(word)
        assert cleaned is None and err is not None

    def test_real_name_still_validates(self):
        from app.engine.validators import DataValidator
        cleaned, err = DataValidator._validate_name("Priya Sharma")
        assert err is None and cleaned == "Priya Sharma"

    def test_graph_validator_rejects_chitchat_name(self, db_session):
        from app.engine.validators import DataValidator
        valid, errors = DataValidator.validate_entities(
            {"name": "thanks"}, list(_fd_map().values()),
            timezone="UTC", max_booking_days=30)
        assert "name" not in valid and errors

    def test_offline_fallback_ignores_chitchat_name(self):
        fds = list(_fd_map().values())
        out = message_processor._extract_entities_without_llm(
            "thanks", ["name"], fds, "UTC", 30)
        assert out.get("name") is None

    async def test_thanks_gets_warm_ack_nothing_saved(self, db_session, monkeypatch):
        """LLM=CHITCHAT + 'thanks!' with name missing: warm reply, name NOT
        saved, flow still asks for the name (no error slap)."""
        org = await _make_org(db_session)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(
            db_session, org, missing=["name", "email", "phone", "date", "time"],
            intent="PROVIDE_DATA", collected={},
            user="wa_9100000081")

        async def _fake_turn(*a, **k):
            return {"intent": "CHITCHAT",
                    "extracted_entities": {"name": "thanks"},
                    "response_message": "You're so welcome! 😊"}
        monkeypatch.setattr(message_processor.llm_engine, "process_turn", _fake_turn)
        action = await message_processor.process_message(
            db_session, org, cfg, sess, "thanks!")
        fresh = await crud_chat.get_chat_session(db_session, sess.session_id)
        assert fresh is not None
        assert (fresh.collected_data or {}).get("name") is None
        assert "name" in (fresh.missing_fields or [])
        assert action.widget_directive != "BOOKING_CONFIRMED_CARD"
        assert "real name" not in (action.message or "").lower()
        assert len(action.message or "") > 0


# ── D. LLM verdict beats keywords ────────────────────────────────────

class TestLLMBeatsKeywords:
    async def test_edit_intent_survives_ok_substring(self, db_session, monkeypatch):
        """'ok change the date to Friday' with LLM=EDIT_DATA must stay edit."""
        org = await _make_org(db_session)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(
            db_session, org, missing=[], intent="CONFIRM_BOOKING",
            collected=dict(FULL_CONTACT, email="d1_edit@example.com"))
        _mock_llm_intent(monkeypatch, "EDIT_DATA")
        action = await message_processor.process_message(
            db_session, org, cfg, sess, "ok change the date to Friday")
        fresh = await crud_chat.get_chat_session(db_session, sess.session_id)
        assert fresh is not None
        assert fresh.current_intent == "EDIT_DATA"
        assert action.widget_directive != "BOOKING_CONFIRMED_CARD"
        assert await _booking_count(db_session, org.id) == 0

    async def test_bare_yes_still_confirms_via_fallback(self, db_session, monkeypatch):
        """LLM indecisive + 'yes' on a complete session must still confirm."""
        org = await _make_org(db_session)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(
            db_session, org, missing=[], intent="CONFIRM_BOOKING",
            collected=dict(FULL_CONTACT, email="d2_yes@example.com"))
        _mock_llm_intent(monkeypatch, "PROVIDE_DATA")
        await message_processor.process_message(db_session, org, cfg, sess, "yes")
        fresh = await crud_chat.get_chat_session(db_session, sess.session_id)
        assert fresh is not None
        assert fresh.current_intent == "CONFIRM_BOOKING"

    async def test_resend_fires_on_indecisive_llm(self, db_session, monkeypatch):
        org = await _make_org(db_session, require_otp=True)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(db_session, org, missing=["phone"],
                                   collected={"name": "R", "email": "d3_rs@example.com"})
        _mock_llm_intent(monkeypatch, "ASK_QUESTION")
        spy = AsyncMock(return_value=ActionPayload(
            message="resent", widget_directive="REQUIRE_OTP_MODAL"))
        monkeypatch.setattr(message_processor, "_handle_otp_resend", spy)
        action = await message_processor.process_message(
            db_session, org, cfg, sess, "resend the code")
        assert spy.await_count == 1
        assert action.widget_directive == "REQUIRE_OTP_MODAL"

    async def test_resend_does_not_hijack_decisive_confirm(self, db_session, monkeypatch):
        """'yes send it' with LLM=CONFIRM must flow to the FRESH email-OTP
        send, never the resend handler."""
        org = await _make_org(db_session, require_otp=True)
        cfg = (await db_session.execute(
            _sel(OrgConfig).where(
                OrgConfig.org_id == org.id))).scalars().first()
        sess = await _make_session(
            db_session, org, missing=[], intent="CONFIRM_BOOKING",
            collected=dict(
                FULL_CONTACT,
                email="d4_hijack@example.com",
                _review_presented=True,
            ))
        _mock_llm_intent(monkeypatch, "CONFIRM_BOOKING")
        spy = AsyncMock(return_value=ActionPayload(
            message="resent", widget_directive="REQUIRE_OTP_MODAL"))
        monkeypatch.setattr(message_processor, "_handle_otp_resend", spy)
        # No SMTP in the test env: mock the mailer so the fresh OTP send
        # succeeds (same pattern as test_booking_otp_context_guard.py).
        monkeypatch.setattr(
            "app.core.mail.send_otp_email",
            AsyncMock(return_value={"success": True, "status": "sent"}),
        )
        action = await message_processor.process_message(
            db_session, org, cfg, sess, "yes send it")
        assert spy.await_count == 0
        assert action.widget_directive == "REQUIRE_OTP_MODAL"
        assert "excellent! i have all your details" in (action.message or "").lower()
