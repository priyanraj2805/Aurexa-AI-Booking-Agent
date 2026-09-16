import pytest
from unittest.mock import AsyncMock, MagicMock


class FakeLLM:
    def __init__(self, ret=None, side_effect=None):
        self._ret = ret
        self._se = side_effect

    async def _call_llm(self, prompt, system_prompt="", json_mode=False):
        if self._se:
            raise self._se
        return self._ret


@pytest.mark.asyncio
async def test_tone_friendly_vs_professional_same_options():
    from app.engine.whatsapp_presenter import WhatsAppPresenter

    directive = {
        "id": "d1",
        "type": "SELECT_DATE",
        "title": "Select your preferred date",
        "description": "We are available from 16 Sep to 16 Oct",
        "options": [
            {"id": "date_20260916", "label": "Wed, Sep 16", "value": "2026-09-16"},
            {"id": "date_20260917", "label": "Thu, Sep 17", "value": "2026-09-17"},
        ],
    }
    # Friendly returns emoji, professional is concise — both preserve options
    llm_friendly = FakeLLM(ret='{"intro": "Sure! 😊 Pick a date that works for you.", "outro": "Just reply with the date!"}')
    p1 = WhatsAppPresenter(llm=llm_friendly)
    r1 = await p1.present_directive(directive=directive, tone="friendly", org_name="TestClinic")
    assert r1.type == "directive"
    assert r1.options == directive["options"]
    assert "Sure" in (r1.intro or "")

    llm_prof = FakeLLM(ret='{"intro": "Please select your preferred date from the options below.", "outro": ""}')
    p2 = WhatsAppPresenter(llm=llm_prof)
    r2 = await p2.present_directive(directive=directive, tone="professional", org_name="TestClinic")
    assert r2.options == directive["options"]
    assert "Please select" in (r2.intro or "")


@pytest.mark.asyncio
async def test_directive_options_immutable_llm_cannot_inject():
    from app.engine.whatsapp_presenter import WhatsAppPresenter

    directive = {
        "id": "d2",
        "type": "SELECT_SLOT",
        "title": "Select your preferred time",
        "description": "Our available booking hours are from 9:00 AM to 5:00 PM",
        "options": [
            {"id": "slot_20260916_0900", "label": "9:00 AM", "value": "09:00:00"},
            {"id": "slot_20260916_1000", "label": "10:00 AM", "value": "10:00:00"},
        ],
    }
    # LLM tries to invent extra option in intro
    llm = FakeLLM(ret='{"intro": "Pick one: 9:00 AM, 10:00 AM, 11:00 AM extra", "outro": ""}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=directive, tone="friendly")
    # Presenter must return original 2 options, not 3
    assert len(r.options) == 2
    assert r.options[0]["id"] == "slot_20260916_0900"
    assert "11:00" not in str(r.options)


@pytest.mark.asyncio
async def test_knowledge_pdf_answer_uses_retrieval():
    from app.engine.whatsapp_presenter import WhatsAppPresenter

    chunks = [
        {"source_type": "pdf", "source_label": "hospital.pdf", "content": "Dr Sharma is cardiology specialist, handles hypertension."}
    ]
    llm = FakeLLM(ret="Dr Sharma is our cardiology specialist and treats hypertension.")
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_knowledge(user_question="Does Dr Sharma treat hypertension?", retrieved_chunks=chunks, tone="friendly", org_name="TestHospital")
    assert r.type == "text"
    assert "Dr Sharma" in (r.body or "")
    assert "hypertension" in (r.body or "").lower()


@pytest.mark.asyncio
async def test_unknown_knowledge_no_hallucination():
    from app.engine.whatsapp_presenter import WhatsAppPresenter

    llm = FakeLLM(ret="Sorry, I don't have that information available right now.")
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_knowledge(user_question="What is your moon service?", retrieved_chunks=[], tone="professional")
    assert "don't have" in (r.body or "").lower()
    # must not invent
    assert "moon" not in (r.body or "").lower() or "don't have" in (r.body or "").lower()


@pytest.mark.asyncio
async def test_fallback_without_llm():
    from app.engine.whatsapp_presenter import WhatsAppPresenter

    directive = {
        "id": "d3",
        "type": "SELECT_DATE",
        "title": "Select your preferred date",
        "description": "We are available from 16 Sep to 16 Oct",
        "options": [{"id": "date_20260916", "label": "Wed, Sep 16", "value": "2026-09-16"}],
    }
    # LLM raises
    llm = FakeLLM(side_effect=TimeoutError("timeout"))
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=directive, tone="friendly")
    assert r.type == "directive"
    assert r.intro is not None and "Select your preferred date" in r.intro
    assert r.options == directive["options"]


@pytest.mark.asyncio
async def test_no_state_mutation():
    from app.engine.whatsapp_presenter import WhatsAppPresenter

    directive = {
        "id": "d4",
        "type": "CONFIRM_BOOKING",
        "title": "Please confirm",
        "description": "Confirm booking for 16 Sep 10:00",
        "options": [{"id": "confirm_booking", "label": "CONFIRM", "value": "confirm"}],
    }
    collected = {"name": "Alice", "date": "2026-09-16"}
    llm = FakeLLM(ret='{"intro": "Looks good!", "outro": ""}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=directive, tone="warm")
    assert collected == {"name": "Alice", "date": "2026-09-16"}  # unchanged
    assert r.type == "directive"
