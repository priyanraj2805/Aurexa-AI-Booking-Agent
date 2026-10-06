from app.engine.cancel_flow import (
    CANCEL_FLOW_KEY,
    extract_contact_from_text,
    is_cancel_exit_request,
    parse_lookup_value,
    reset_for_new_booking,
)
from app.schemas.agent import ChatSessionState


class TestIsCancelExitRequest:
    def test_new_booking_requests_exit_lookup(self) -> None:
        for msg in (
            "it okay let it be now book an new appointment",
            "it okat book a new appointment",
            "book an appointment",
            "book a new appointment",
            "i want to book",
            "let's book a meeting",
        ):
            assert is_cancel_exit_request(msg), f"expected exit intent for: {msg}"

    def test_abandon_phrases_exit_lookup(self) -> None:
        for msg in (
            "never mind",
            "nevermind",
            "changed my mind",
            "no worries",
            "let it be",
            "forget it",
            "skip it",
            "cancel this",
            "no i don't want to cancel",
        ):
            assert is_cancel_exit_request(msg), f"expected exit intent for: {msg}"

    def test_it_is_ok_phrases_exit(self) -> None:
        for msg in ("it's ok", "it okay", "it is fine", "it's alright"):
            assert is_cancel_exit_request(msg), f"expected exit intent for: {msg}"

    def test_bare_cancel_is_cancel_intent(self) -> None:
        """Production: bare "Cancel" matched no pattern, so routing fell to
        the LLM verdict, which flip-flopped across identical turns (summary
        card, farewell, summary, lookup). A whole-message "cancel" is
        unambiguous and routes deterministically."""
        from app.engine.cancel_flow import is_cancel_intent

        for text in ("Cancel", "cancel", "CANCEL", "Cancel.", "  cancel  "):
            assert is_cancel_intent(text) is True

    def test_real_cancel_intents_do_not_exit(self) -> None:
        for msg in (
            "cancel my booking",
            "cancel this appointment",
            "i want to cancel my appointment",
            "please cancel",
            "cancel this reservation please",
        ):
            assert not is_cancel_exit_request(msg), f"unexpected exit intent for: {msg}"

    def test_contact_values_do_not_exit(self) -> None:
        for msg in (
            "priyanthakur3020@gmail.com",
            "+91 98765 43210",
            "9876543210",
        ):
            assert not is_cancel_exit_request(msg), f"unexpected exit intent for: {msg}"

    def test_empty_message_does_not_exit(self) -> None:
        assert not is_cancel_exit_request("")
        assert not is_cancel_exit_request("   ")


class TestLookupPriority:
    """Contact parsing must win over exit heuristics for email/phone input."""

    def test_email_still_lookup_value(self) -> None:
        assert parse_lookup_value("priyanthakur3020@gmail.com") == (
            "email",
            "priyanthakur3020@gmail.com",
        )

    def test_phone_still_lookup_value(self) -> None:
        parsed = parse_lookup_value("+91 98765 43210")
        assert parsed is not None
        kind, value = parsed
        assert kind == "phone"
        assert value == "919876543210"

    def test_non_contact_still_not_parsed(self) -> None:
        assert parse_lookup_value("book a new appointment") is None


class TestExtractContactFromText:
    def test_extracts_embedded_email(self) -> None:
        parsed = extract_contact_from_text("my email is priyan@test.com")
        assert parsed is not None
        kind, value = parsed
        assert kind == "email"
        assert value == "priyan@test.com"

    def test_extracts_embedded_phone(self) -> None:
        parsed = extract_contact_from_text("you can call me at 98765 43210")
        assert parsed is not None
        kind, value = parsed
        assert kind == "phone"
        assert value == "9876543210"

    def test_plain_email_extracted(self) -> None:
        parsed = extract_contact_from_text("priyan@test.com")
        assert parsed is not None
        kind, value = parsed
        assert kind == "email"
        assert value == "priyan@test.com"

    def test_otp_code_not_treated_as_contact(self) -> None:
        assert extract_contact_from_text("123456") is None

    def test_no_contact_returns_none(self) -> None:
        assert extract_contact_from_text("thanks for your help!") is None
        assert extract_contact_from_text("") is None


class TestLlmCancelFlowAnalysis:
    async def _run(self, monkeypatch, payload: str) -> dict:
        from app.engine import cancel_flow, message_processor

        class _FakeEngine:
            async def _call_llm(self, prompt, system_prompt="", json_mode=False):
                return payload

        monkeypatch.setattr(message_processor, "llm_engine", _FakeEngine())
        return await cancel_flow._llm_cancel_flow_analysis(
            "thanks!", stage="lookup", flow={}
        )

    async def test_classifies_smalltalk_with_reply(self, monkeypatch) -> None:
        result = await self._run(
            monkeypatch,
            '{"classification": "SMALLTALK", "confidence": 0.9, "reply": "You\'re welcome!"}',
        )
        assert result["classification"] == "SMALLTALK"
        assert result["reply"] == "You're welcome!"

    async def test_classifies_exit_flow(self, monkeypatch) -> None:
        result = await self._run(
            monkeypatch,
            '{"classification": "EXIT_FLOW", "confidence": 0.95, "reply": ""}',
        )
        assert result["classification"] == "EXIT_FLOW"

    async def test_classifies_contact_value(self, monkeypatch) -> None:
        result = await self._run(
            monkeypatch,
            '{"classification": "CONTACT_VALUE", "confidence": 0.9, "reply": ""}',
        )
        assert result["classification"] == "CONTACT_VALUE"

    async def test_falls_back_to_smalltalk_on_llm_failure(self, monkeypatch) -> None:
        from app.engine import cancel_flow, message_processor

        class _BrokenEngine:
            async def _call_llm(self, prompt, system_prompt="", json_mode=False):
                raise RuntimeError("provider down")

        monkeypatch.setattr(message_processor, "llm_engine", _BrokenEngine())
        result = await cancel_flow._llm_cancel_flow_analysis(
            "hello?", stage="otp", flow={}
        )
        assert result["classification"] == "SMALLTALK"
        assert result["reply"] == ""

    async def test_falls_back_when_llm_returns_garbage(self, monkeypatch) -> None:
        result = await self._run(monkeypatch, "not json at all")
        assert result["classification"] == "SMALLTALK"

    async def test_unknown_classification_normalized_to_smalltalk(
        self, monkeypatch
    ) -> None:
        result = await self._run(
            monkeypatch, '{"classification": "WEIRD", "confidence": 0.5, "reply": "hi"}'
        )
        assert result["classification"] == "SMALLTALK"
        assert result["reply"] == "hi"


class TestResetForNewBooking:
    def test_clears_flow_and_restores_start_booking(self) -> None:
        session = ChatSessionState(
            session_id="00000000-0000-0000-0000-000000000001",
            org_id="00000000-0000-0000-0000-000000000000",
            collected_data={
                "name": "Priyanshu",
                CANCEL_FLOW_KEY: {"stage": "lookup"},
            },
            missing_fields=["date", "time"],
            current_intent="CANCEL_BOOKING",
            session_status="CONFIRMING",
            otp_verified=True,
            pending_cancel=True,
        )
        reset_for_new_booking(session)

        assert CANCEL_FLOW_KEY not in session.collected_data
        assert session.collected_data == {}
        assert session.missing_fields == []
        assert session.current_intent == "START_BOOKING"
        assert session.session_status == "ACTIVE"
        assert session.otp_verified is False
        assert session.pending_cancel is False

    def test_keeps_snapshot_only(self) -> None:
        from app.crud import chat as crud_chat

        session = ChatSessionState(
            session_id="00000000-0000-0000-0000-000000000002",
            org_id="00000000-0000-0000-0000-000000000000",
            collected_data={
                crud_chat.FIELD_DEFINITIONS_SNAPSHOT_KEY: {"field_definitions": []},
                crud_chat.RUNTIME_CHECKPOINT_KEY: {"summary": "s"},
                "email": "a@b.com",
                CANCEL_FLOW_KEY: {"stage": "otp"},
            },
        )
        reset_for_new_booking(session)

        assert crud_chat.FIELD_DEFINITIONS_SNAPSHOT_KEY in session.collected_data
        assert crud_chat.RUNTIME_CHECKPOINT_KEY not in session.collected_data
        assert "email" not in session.collected_data