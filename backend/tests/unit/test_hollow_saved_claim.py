"""Hollow saved-claim: the reply asserts a field is on file while it is
still missing AND nothing failed validation (the older guard only fires
on rejected values).

Production (WhatsApp): "Thanks, I have your phone number on file. Could
you please share your preferred date?" — phone was never saved, the
customer believed it was done, and the flow asked for it forever.
"""

from app.engine.langgraph_orchestrator import _guard_user_visible_booking_message
from app.schemas.agent import ChatSessionState, FieldDefinition


def _fd(name, label, required=True):
    return FieldDefinition(
        name=name,
        type="text",
        label=label,
        required=required,
        order=0,
        options=[],
        placeholder="",
        description="",
        icon="",
    )


def _session(collected):
    return ChatSessionState(
        session_id="00000000-0000-0000-0000-000000000001",
        org_id="00000000-0000-0000-0000-000000000002",
        channel="web",
        collected_data=dict(collected),
        missing_fields=[],
        current_intent="PROVIDE_DATA",
    )


def _fd_map():
    return {
        "name": _fd("name", "Full Name"),
        "email": _fd("email", "Email Address"),
        "phone": _fd("phone", "Phone Number"),
        "date": _fd("date", "Preferred Date"),
        "time": _fd("time", "Preferred Time"),
    }


def test_hollow_phone_claim_becomes_phone_ask():
    out = _guard_user_visible_booking_message(
        assistant_message=(
            "Thanks, I have your phone number on file. "
            "Could you please share your preferred date?"
        ),
        directive="TEXT_INPUT",
        require_otp=False,
        chat_session=_session({"name": "X", "email": "x@y.z"}),
        field_meta={},
        fd_map=_fd_map(),
    )
    assert "phone number" in out.lower()
    assert "on file" not in out.lower()


def test_true_claim_about_saved_field_passes_through():
    out = _guard_user_visible_booking_message(
        assistant_message="Thanks, I've got your email. What date works?",
        directive="TEXT_INPUT",
        require_otp=False,
        chat_session=_session(
            {"name": "X", "email": "x@y.z", "phone": "+911234567890"}
        ),
        field_meta={},
        fd_map=_fd_map(),
    )
    assert out == "Thanks, I've got your email. What date works?"


def test_unrelated_answer_passes_through():
    out = _guard_user_visible_booking_message(
        assistant_message="We are open 9 to 5 on weekdays.",
        directive="TEXT_INPUT",
        require_otp=False,
        chat_session=_session({"name": "X"}),
        field_meta={},
        fd_map=_fd_map(),
    )
    assert out == "We are open 9 to 5 on weekdays."
