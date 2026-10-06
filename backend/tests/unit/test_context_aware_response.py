"""Tests for the deterministic response builder and fallback extraction."""

import pytest
from app.engine.message_processor import (
    _build_context_aware_response,
    _build_confirmation_summary,
    _extract_entities_without_llm,
    _should_attempt_fallback_extraction,
    _looks_like_name_candidate,
)
from app.schemas.agent import FieldDefinition, ChatSessionState


class TestBuildContextAwareResponse:
    def test_requires_email_with_name(self):
        msg = _build_context_aware_response(
            directive="REQUIRE_EMAIL_INPUT",
            field_meta={"name": "email", "label": "Email Address"},
            collected_data={"name": "John Doe"},
            valid_data={},
            errors={},
            llm_generated_msg="whatever",
        )
        assert "verification code" not in msg.lower()
        assert msg == ""

    def test_requires_select_with_name(self):
        msg = _build_context_aware_response(
            directive="REQUIRE_SELECT",
            field_meta={"name": "org_size", "label": "Organisation Size"},
            collected_data={"name": "Jane"},
            valid_data={},
            errors={},
            llm_generated_msg="whatever",
        )
        assert "Jane" in msg
        assert "organisation size" in msg.lower()

    def test_requires_text_input_for_custom_field(self):
        msg = _build_context_aware_response(
            directive="TEXT_INPUT",
            field_meta={"name": "organisation_name", "label": "Organisation Name"},
            collected_data={"name": "Devang"},
            valid_data={},
            errors={},
            llm_generated_msg="whatever",
        )
        assert "Devang" in msg
        assert "organisation name" in msg.lower()

    def test_requires_date_picker(self):
        msg = _build_context_aware_response(
            directive="REQUIRE_DATE_PICKER",
            field_meta={"name": "date", "label": "Preferred Date"},
            collected_data={"name": "Test"},
            valid_data={},
            errors={},
            llm_generated_msg="whatever",
        )
        assert "Test" in msg
        assert "date" in msg.lower()

    def test_requires_time_slot_grid_with_date(self):
        msg = _build_context_aware_response(
            directive="REQUIRE_TIME_SLOT_GRID",
            field_meta={"name": "time", "label": "Preferred Time"},
            collected_data={"name": "Alice", "date": "2026-04-24"},
            valid_data={},
            errors={},
            llm_generated_msg="whatever",
        )
        assert "Alice" in msg
        assert "April" in msg

    def test_returns_llm_msg_for_confirmation(self):
        msg = _build_context_aware_response(
            directive="REQUEST_CONFIRMATION",
            field_meta=None,
            collected_data={},
            valid_data={},
            errors={},
            llm_generated_msg="Here is your summary",
        )
        assert msg == "Here is your summary"


class TestExtractEntitiesWithoutLlm:
    def setup_method(self):
        self.field_defs = [
            FieldDefinition(name="name", type="text", label="Full Name", order=0),
            FieldDefinition(name="email", type="email", label="Email", order=1),
            FieldDefinition(
                name="organisation_name",
                type="text",
                label="Organisation Name",
                order=2,
            ),
            FieldDefinition(
                name="organisation_head_count",
                type="select",
                label="Org Size",
                options=["11 - 50", "50 - 200", "200+"],
                order=3,
            ),
            FieldDefinition(name="phone", type="phone", label="Phone", order=4),
            FieldDefinition(name="date", type="date", label="Date", order=5),
            FieldDefinition(name="time", type="time", label="Time", order=6),
        ]

    def test_extracts_custom_text_field(self):
        result = _extract_entities_without_llm(
            message_text="Celebal Technologies",
            missing_fields=["organisation_name"],
            field_definitions=self.field_defs,
            timezone_name="UTC",
            max_booking_days=90,
        )
        assert "organisation_name" in result
        assert result["organisation_name"] == "Celebal Technologies"

    def test_extracts_email(self):
        result = _extract_entities_without_llm(
            message_text="test@example.com",
            missing_fields=["email"],
            field_definitions=self.field_defs,
            timezone_name="UTC",
            max_booking_days=90,
        )
        assert "email" in result

    def test_extracts_phone(self):
        result = _extract_entities_without_llm(
            message_text="9772889266",
            missing_fields=["phone"],
            field_definitions=self.field_defs,
            timezone_name="UTC",
            max_booking_days=90,
        )
        assert "phone" in result

    def test_rejects_greeting_as_name(self):
        result = _extract_entities_without_llm(
            message_text="hiii",
            missing_fields=["name"],
            field_definitions=self.field_defs,
            timezone_name="UTC",
            max_booking_days=90,
        )
        assert "name" not in result

    def test_intro_sentence_never_becomes_a_name(self):
        """"I am Rohan Verma" was once validated whole and BOOKED verbatim.
        Parsing introductions belongs to the model; the deterministic
        fallback must capture nothing here so the flow safely re-asks."""
        result = _extract_entities_without_llm(
            message_text="I am Rohan Verma",
            missing_fields=["name"],
            field_definitions=self.field_defs,
            timezone_name="UTC",
            max_booking_days=90,
        )
        assert "name" not in result

    def test_question_never_becomes_name(self):
        result = _extract_entities_without_llm(
            message_text="what is the capital of France?",
            missing_fields=["name"],
            field_definitions=self.field_defs,
            timezone_name="UTC",
            max_booking_days=90,
        )
        assert "name" not in result

    def test_rejects_greeting_as_custom_field(self):
        result = _extract_entities_without_llm(
            message_text="hello",
            missing_fields=["organisation_name"],
            field_definitions=self.field_defs,
            timezone_name="UTC",
            max_booking_days=90,
        )
        assert "organisation_name" not in result

    def test_extracts_select_field(self):
        result = _extract_entities_without_llm(
            message_text="50 - 200",
            missing_fields=["organisation_head_count"],
            field_definitions=self.field_defs,
            timezone_name="UTC",
            max_booking_days=90,
        )
        assert "organisation_head_count" in result


class TestShouldAttemptFallbackExtraction:
    def test_empty_entities_with_missing_fields(self):
        assert (
            _should_attempt_fallback_extraction(
                response_message="I see. Please tell me more.",
                extracted_entities={},
                missing_fields=["organisation_name"],
            )
            is True
        )

    def test_empty_entities_no_missing_fields(self):
        assert (
            _should_attempt_fallback_extraction(
                response_message="All done!",
                extracted_entities={},
                missing_fields=[],
            )
            is False
        )

    def test_has_entities_no_fallback(self):
        assert (
            _should_attempt_fallback_extraction(
                response_message="Got it!",
                extracted_entities={"name": "John"},
                missing_fields=["email"],
            )
            is False
        )

    def test_fallback_phrase_triggers(self):
        assert (
            _should_attempt_fallback_extraction(
                response_message="I'm having trouble connecting to my brain",
                extracted_entities={},
                missing_fields=["email"],
            )
            is True
        )

    def test_short_response_triggers(self):
        assert (
            _should_attempt_fallback_extraction(
                response_message="Ok",
                extracted_entities={},
                missing_fields=["organisation_name"],
            )
            is True
        )


class TestLooksLikeNameCandidate:
    def test_greetings_rejected(self):
        for g in ["hi", "hii", "hiii", "hello", "hey", "yo", "sup"]:
            assert _looks_like_name_candidate(g) is False, f"'{g}' should be rejected"

    def test_real_name_accepted(self):
        assert _looks_like_name_candidate("John Doe") is True
        assert _looks_like_name_candidate("Devang Bhardwaj") is True

    def test_numbers_rejected(self):
        assert _looks_like_name_candidate("test123") is False


class TestConfirmationSummaryWhitelist:
    def test_confirmation_summary_excludes_internal_runtime_keys(self):
        fd_map = {
            "name": FieldDefinition(name="name", type="text", label="Full Name", icon="👤", order=0),
            "email": FieldDefinition(name="email", type="email", label="Email", icon="✉️", order=1),
        }
        session = ChatSessionState(
            session_id="session-summary",
            org_id="org-summary",
            collected_data={
                "name": "Jane Doe",
                "email": "jane@example.com",
                "field_definitions_snapshot": [{"name": "name"}],
                "runtime_checkpoint": {"state": {"summary": "internal"}},
                "selected_slot_id": "slot-internal-id",
                "_internal": {"blob": True},
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
        )

        summary, directive = _build_confirmation_summary(session, fd_map)

        assert directive == "REQUEST_CONFIRMATION"
        assert "Jane Doe" in summary
        assert "jane@example.com" in summary
        assert "field_definitions_snapshot" not in summary
        assert "runtime_checkpoint" not in summary
        assert "selected_slot_id" not in summary
