"""Validator corrections must be spoken on WhatsApp, not just attached.

The engine composes its text BEFORE validation runs, and WhatsApp has no
inline-validation UI (unlike the web widget), so a rejected value's precise
reason has to travel inside the message body.
"""
from types import SimpleNamespace

from app.core.whatsapp_client import format_action_for_whatsapp


def _action(message, directive="TEXT_INPUT", field_meta=None):
    return SimpleNamespace(
        message=message, widget_directive=directive, field_meta=field_meta or {}
    )


def test_validation_error_prepended_to_body():
    out = format_action_for_whatsapp(
        _action(
            "Thank you. Please share your email address.",
            "TEXT_INPUT",
            {"validation_error": "Invalid email: missing @."},
        )
    )
    assert out["type"] == "text"
    assert out["body"].startswith("Invalid email: missing @.")
    assert "Please share your email address." in out["body"]


def test_no_duplication_when_body_already_carries_it():
    out = format_action_for_whatsapp(
        _action(
            "Invalid email: missing @. Please try again.",
            "TEXT_INPUT",
            {"validation_error": "Invalid email: missing @."},
        )
    )
    assert out["body"].count("Invalid email: missing @.") == 1


def test_no_correction_without_error_unchanged():
    out = format_action_for_whatsapp(_action("Hello!", "TEXT_INPUT", {}))
    assert out == {"type": "text", "body": "Hello!"}


def test_correction_flows_into_interactive_list_body():
    out = format_action_for_whatsapp(
        _action(
            "Pick a time",
            "REQUIRE_TIME_SLOT_GRID",
            {
                "options": ["10:00 AM", "11:00 AM"],
                "validation_error": "That slot is no longer available.",
            },
        )
    )
    assert out["type"] == "interactive_list"
    assert "That slot is no longer available." in out["body"]


def test_em_and_en_dashes_rewritten():
    out = format_action_for_whatsapp(
        _action("Slots open 10–11 AM — reply fast — today", "TEXT_INPUT", {})
    )
    assert "—" not in out["body"]
    assert "–" not in out["body"]
    assert "10-11 AM" in out["body"]


def test_knowledge_answer_combines_with_step():
    from app.core.whatsapp_client import attach_knowledge_answer

    combined = attach_knowledge_answer(
        "DotStark is an AI consultancy in Jaipur.",
        "Please choose your preferred date for the appointment.",
    )
    assert combined.startswith("DotStark is an AI consultancy in Jaipur.")
    assert "preferred date" in combined


def test_knowledge_combine_noops_on_empty_or_dup():
    from app.core.whatsapp_client import attach_knowledge_answer

    assert attach_knowledge_answer("", "Pick a date") == "Pick a date"
    assert attach_knowledge_answer("Pick a date", "Pick a date") == "Pick a date"
