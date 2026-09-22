"""Mid-booking objection handling: acknowledge in admin tone, never ignore.

Covers app.engine.whatsapp_guards detection/brief builders and the
WhatsAppPresenter.present_objection deterministic fallback (llm=False).
Live slots only — business name, labels, steps all come from arguments.
"""
from app.engine import whatsapp_guards as guards
from app.engine.whatsapp_presenter import WhatsAppPresenter

import asyncio


def test_why_needed_with_field():
    hit = guards.detect_objection(
        "why do you need my phone?", {"name": "Asha"}, "phone"
    )
    assert hit is not None
    assert hit["category"] == "why_needed"
    assert hit["field"] == "phone"


def test_why_needed_defaults_to_step_field():
    hit = guards.detect_objection("why is this required?", {}, "date")
    assert hit is not None
    assert hit["category"] == "why_needed"
    assert hit["field"] == "date"


def test_refuse_field():
    hit = guards.detect_objection(
        "I don't want to give my email", {"name": "Asha"}, "email"
    )
    assert hit is not None
    assert hit["category"] == "refuse"
    assert hit["field"] == "email"


def test_no_booking():
    hit = guards.detect_objection("I don't want to book anymore", {}, "date")
    assert hit is not None
    assert hit["category"] == "no_booking"


def test_privacy_concern():
    hit = guards.detect_objection("is my data safe with you?", {}, "phone")
    assert hit is not None
    assert hit["category"] == "privacy"


def test_non_objections_pass_through():
    collected = {"name": "Asha"}
    assert guards.detect_objection("19 Sep", collected, "date") is None
    assert guards.detect_objection("2", collected, "date") is None
    assert guards.detect_objection("what are your hours?", collected, "date") is None
    assert guards.detect_objection("hello there", collected, None) is None
    assert guards.detect_objection("change the date", collected, "time") is None
    assert guards.detect_objection("CONFIRM", collected, None) is None
    assert guards.detect_objection("", collected, "date") is None


def test_field_slot_from_live_defs():
    defs = [
        {"name": "phone", "label": "Phone Number", "description": "To send booking updates", "required": True},
        {"name": "email", "label": "Email Address", "description": "", "required": False},
    ]
    label, desc, required = guards.field_slot(defs, "phone")
    assert (label, desc, required) == ("Phone Number", "To send booking updates", True)
    label2, _, required2 = guards.field_slot(defs, "email")
    assert (label2, required2) == ("Email Address", False)
    assert guards.required_labels(defs) == ["Phone Number"]
    # Unknown field: neutral fallback, never invented copy.
    assert guards.field_slot(defs, "mystery") == ("Mystery", "", True)


def test_brief_echoes_live_inputs():
    brief = guards.build_objection_brief(
        category="why_needed",
        field="phone",
        business_name="Acme Clinic",
        tone="empathetic",
        field_label="Phone Number",
        field_description="To send booking updates",
        field_required=True,
        required_labels=["Full Name", "Phone Number"],
        step_title="Share your phone number",
        step_prompt="Please type your 10-digit mobile number.",
    )
    assert brief["business_name"] == "Acme Clinic"
    assert brief["tone"] == "empathetic"
    assert brief["field_label"] == "Phone Number"
    assert brief["step_title"] == "Share your phone number"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _body_of(brief) -> str:
    resp = _run(WhatsAppPresenter(llm=False).present_objection(brief))
    assert resp.type == "text"
    assert resp.body is not None
    return resp.body


def test_presenter_fallback_uses_live_slots():
    brief = guards.build_objection_brief(
        category="why_needed",
        field="phone",
        business_name="Acme Clinic",
        tone="empathetic",
        field_label="Phone Number",
        field_description="To send booking updates",
        field_required=True,
        required_labels=["Full Name", "Phone Number"],
        step_title="Share your phone number",
        step_prompt="Please type your 10-digit mobile number.",
    )
    resp = _run(WhatsAppPresenter(llm=False).present_objection(brief))
    assert resp.type == "text"
    body = resp.body or ""
    assert "Acme Clinic" in body
    assert "Phone Number" in body
    assert "To send booking updates" in body
    assert "10-digit" in body  # step re-anchor survives
    assert len(body) <= 1200


def test_presenter_fallback_no_booking():
    brief = guards.build_objection_brief(
        category="no_booking",
        field=None,
        business_name="Acme Clinic",
        tone="casual",
        step_title="",
        step_prompt="",
    )
    resp = _run(WhatsAppPresenter(llm=False).present_objection(brief))
    assert resp.type == "text"
    body = resp.body or ""
    assert "Acme Clinic" in body
    assert "say hi" in body.lower()


def _brief_for(category: str):
    return guards.build_objection_brief(
        category=category,
        field="email",
        business_name="Acme Clinic",
        tone="formal",
        field_label="Email Address",
        field_description="",
        field_required=True,
        required_labels=["Email Address"],
        step_title="Share your email",
        step_prompt="Please type your email address.",
    )


def test_presenter_fallback_privacy_and_refuse():
    priv = _run(WhatsAppPresenter(llm=False).present_objection(_brief_for("privacy")))
    assert priv.type == "text"
    priv_body = priv.body or ""
    assert "Acme Clinic" in priv_body
    assert "Share your email" in priv_body
    ref = _run(WhatsAppPresenter(llm=False).present_objection(_brief_for("refuse")))
    assert ref.type == "text"
    ref_body = ref.body or ""
    assert "Email Address" in ref_body
