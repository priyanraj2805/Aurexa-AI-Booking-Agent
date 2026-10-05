"""A required field must never be waived — by the user, the model, or code.

Production manipulation: "bina email address ke proceed krte hai please"
-> "I can skip that and ask for your phone number instead." then "also
skip contact number" -> "I can skip the phone number too." Both contacts
are REQUIRED, yet the bot agreed to drop them and marched on to the date.
Missing stayed correct ([email, phone, ...]) while the reply lied — so the
fix has three layers: catch the refusal (incl. Hinglish), never emit the
waiver, and pin it in the prompt.
"""
import pytest

from app.engine.decline_flow import is_field_refusal
from app.engine.user_visible_text import looks_like_field_waiver


@pytest.mark.parametrize(
    "text",
    [
        "bina email address ke proceed krte hai please",
        "bina phone ke aage badho",
        "email ke bina booking karo",
        "also skip contact number",
        "skip my phone",
        "skip kar do",
        "number rehne do",
        "email mat mango",
        "proceed without email",
        "continue without phone",
        "no need for email",
        "I don't want to share my email",
    ],
)
def test_refusal_shapes_detected(text: str) -> None:
    assert is_field_refusal(text, ["email", "phone"]) is True


@pytest.mark.parametrize(
    "text",
    [
        "my email is test@example.com",
        "9814732658",
        "yes proceed",
        "proceed",
        "thanks",
        "What time works?",
    ],
)
def test_non_refusals_not_detected(text: str) -> None:
    assert is_field_refusal(text, ["email", "phone"]) is False


@pytest.mark.parametrize(
    "reply",
    [
        "I understand. I can skip the phone number too.",
        "I can skip that and ask for your phone number instead.",
        "No problem, we can proceed without the email.",
        "Sure, no need for your phone, let's continue.",
    ],
)
def test_waiver_replies_detected_when_required_pending(reply: str) -> None:
    assert (
        looks_like_field_waiver(
            reply,
            missing_fields=["email", "phone", "date"],
            required_names=["name", "email", "phone", "date", "time"],
        )
        is True
    )


def test_optional_waiver_passes() -> None:
    """Waiving an actually-optional field is legal and must pass."""
    assert (
        looks_like_field_waiver(
            "No problem, we can skip that.",
            missing_fields=["gender"],
            required_names=["name", "email", "phone", "date", "time"],
        )
        is False
    )


def test_no_missing_no_waiver() -> None:
    assert (
        looks_like_field_waiver(
            "I can skip that.",
            missing_fields=[],
            required_names=["email"],
        )
        is False
    )


def test_graph_guard_replaces_waiver_with_firm_ask() -> None:
    """End of the chain: the orchestrator guard swaps the waiver reply for
    a firm re-ask of the first still-missing required field."""
    from types import SimpleNamespace

    from app.engine.langgraph_orchestrator import _guard_user_visible_booking_message
    from app.schemas.agent import FieldDefinition

    session = SimpleNamespace(
        collected_data={"name": "Test"},
        missing_fields=["phone", "date"],
        otp_verified=False,
        phone_otp_verified=False,
    )
    fd_map = {
        "phone": FieldDefinition(
            name="phone", type="phone", label="Phone Number", required=True
        ),
        "date": FieldDefinition(
            name="date", type="date", label="Preferred Date", required=True
        ),
    }
    out = _guard_user_visible_booking_message(
        assistant_message="I understand. I can skip the phone number too.",
        directive="TEXT_INPUT",
        require_otp=False,
        chat_session=session,
        field_meta={"name": "phone", "label": "Phone Number"},
        fd_map=fd_map,
    )
    assert "skip" not in out.lower()
    assert "phone" in out.lower()
