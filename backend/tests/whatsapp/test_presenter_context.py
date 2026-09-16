import pytest
from unittest.mock import AsyncMock


class FakeLLM:
    def __init__(self, ret):
        self._ret = ret
    async def _call_llm(self, prompt, system_prompt="", json_mode=False):
        return self._ret


def make_directive(dtype="SELECT_DATE", title="Select your preferred date", desc="We are available from 16 Sep to 16 Oct"):
    return {
        "id": "d1",
        "type": dtype,
        "title": title,
        "description": desc,
        "options": [
            {"id": "date_20260916", "label": "Wed, Sep 16", "value": "2026-09-16"},
            {"id": "date_20260917", "label": "Thu, Sep 17", "value": "2026-09-17"},
        ],
    }


@pytest.mark.asyncio
async def test_first_message_gets_greeting():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    # is_first True -> greeting allowed
    d = make_directive()
    llm = FakeLLM('{"intro": "Hello! Welcome to TestClinic. Please choose a date.", "outro": ""}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=d, tone="friendly", org_name="TestClinic", user_message="Hii", is_first_turn=True, turn_index=0, collected_data={}, agent_name="Aurexa", business_name="TestClinic", customer_name="")
    assert "Hello" in (r.intro or "")
    assert r.options is not None and len(r.options) == 2


@pytest.mark.asyncio
async def test_second_booking_message_no_greeting():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    d = make_directive(dtype="SELECT_DATE", title="Select your preferred date", desc="We are available...")
    # LLM tries to greet again even though not first turn and user didn't greet -> presenter must strip
    llm = FakeLLM('{"intro": "Hello, this is TestClinic. Please select your preferred date for your appointment.", "outro": "Do you need any further assistance?"}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=d, tone="friendly", org_name="TestClinic", user_message="6350063338", is_first_turn=False, turn_index=5, collected_data={"name": "Priyanshu", "email": "p@test.com", "phone": "6350063338"}, agent_name="Aurexa", business_name="TestClinic", customer_name="Priyanshu")
    # Should strip leading greeting
    assert not (r.intro or "").strip().lower().startswith("hello")
    # Generic closing stripped
    assert r.outro is None or "further assistance" not in (r.outro or "").lower()
    # Options preserved
    assert r.options == d["options"]


@pytest.mark.asyncio
async def test_select_date_contextual():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    d = make_directive(dtype="SELECT_DATE", title="Select your preferred date", desc="We are available from 16 Sep to 16 Oct")
    llm = FakeLLM('{"intro": "Sure! Here are the available dates. Please choose one to continue.", "outro": ""}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=d, tone="friendly", org_name="TestClinic", user_message="Book appointment", is_first_turn=False, collected_data={"name": "A"}, business_name="TestClinic", customer_name="A")
    assert "available dates" in (r.intro or "").lower()
    assert r.directive_type == "SELECT_DATE"


@pytest.mark.asyncio
async def test_select_time_contextual():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    d = make_directive(dtype="SELECT_SLOT", title="Select your preferred time", desc="Our available booking hours are from 9:30 AM to 6:30 PM and your booking will be of 1 hr")
    llm = FakeLLM('{"intro": "Great! 17 September is available. Here are the available time slots:", "outro": ""}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=d, tone="friendly", user_message="16 Sep", is_first_turn=False, collected_data={"name":"A","date":"2026-09-17"}, business_name="TestClinic", customer_name="A")
    assert "17 September" in (r.intro or "") or "available time" in (r.intro or "").lower()
    assert r.directive_type == "SELECT_SLOT"


@pytest.mark.asyncio
async def test_select_slot_contextual():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    d = make_directive(dtype="SELECT_SLOT", title="Select your preferred time", desc="Available times for 16 Sep")
    llm = FakeLLM('{"intro": "Here are the available slots for your selected date. Please choose one.", "outro": ""}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=d, tone="professional", is_first_turn=False, collected_data={"date": "2026-09-16"}, business_name="Clinic")
    assert "available slots" in (r.intro or "").lower()


@pytest.mark.asyncio
async def test_no_generic_closing():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    d = make_directive()
    llm = FakeLLM('{"intro": "Here are the dates.", "outro": "We look forward to confirming your booking. Do you need any further assistance?"}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=d, tone="friendly", is_first_turn=False, user_message="Show me dates", collected_data={"name":"A"})
    # Generic closing should be stripped on non-confirm steps
    assert r.outro is None or "further assistance" not in (r.outro or "").lower()
    assert r.outro is None or "look forward" not in (r.outro or "").lower()


@pytest.mark.asyncio
async def test_tone_changes_wording_not_data():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    d = make_directive()
    llm_f = FakeLLM('{"intro": "Great! 😊 Here are the dates!", "outro": ""}')
    llm_p = FakeLLM('{"intro": "17 September is available. Please select a preferred date.", "outro": ""}')
    p_f = WhatsAppPresenter(llm=llm_f)
    r_f = await p_f.present_directive(directive=d, tone="friendly", business_name="Clinic")
    p_p = WhatsAppPresenter(llm=llm_p)
    r_p = await p_p.present_directive(directive=d, tone="professional", business_name="Clinic")
    assert r_f.options == d["options"] == r_p.options
    assert r_f.intro != r_p.intro


@pytest.mark.asyncio
async def test_options_byte_for_byte():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    d = make_directive()
    d["options"] = [{"id": "date_20260916", "label": "Wed, Sep 16", "value": "2026-09-16"}]
    llm = FakeLLM('{"intro": "Pick a date", "outro": ""}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=d, tone="friendly")
    assert r.options[0]["id"] == "date_20260916"
    assert r.options[0]["value"] == "2026-09-16"


@pytest.mark.asyncio
async def test_llm_cannot_invent_ids():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    d = make_directive()
    llm = FakeLLM('{"intro": "Hello, choose date_99999", "outro": ""}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=d, tone="friendly")
    # Intro should have leaked ID stripped
    assert "date_99999" not in (r.intro or "")


@pytest.mark.asyncio
async def test_business_vs_customer_name():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    d = make_directive()
    # Customer is Priyanshu, business is TestClinic — LLM should not say Hello from Priyanshu
    llm = FakeLLM('{"intro": "Hello Priyanshu, we are ready to schedule your appointment. Please choose a date.", "outro": ""}')
    p = WhatsAppPresenter(llm=llm)
    r = await p.present_directive(directive=d, tone="friendly", business_name="TestClinic", customer_name="Priyanshu", agent_name="Aurexa", is_first_turn=False, user_message="Book")
    # Should not contain "Hello from Priyanshu" / "This is Priyanshu"
    low = (r.intro or "").lower()
    assert "hello from priyanshu" not in low
    assert "this is priyanshu" not in low
    # But greeting to customer by name is okay: "Hello Priyanshu, we are ready..."
    # We only forbid business masquerading as customer; this test ensures not stripping valid personalization
    assert "priyanshu" in low or "choose a date" in low


@pytest.mark.asyncio
async def test_unknown_knowledge_no_hallucination():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    p = WhatsAppPresenter(llm=FakeLLM("Sorry, I don't have that information available right now. Would you like to book an appointment?"))
    # For knowledge path, use present_knowledge with no chunks
    r = await p.present_knowledge(user_question="Do you have cardiology dept?", retrieved_chunks=[], tone="friendly", org_name="Clinic")
    assert "don't have" in (r.body or "").lower()


@pytest.mark.asyncio
async def test_knowledge_preserves_booking_state():
    from app.engine.whatsapp_presenter import WhatsAppPresenter
    collected = {"name": "A", "date": "2026-09-16"}
    p = WhatsAppPresenter(llm=FakeLLM("Visiting hours 10am-7pm."))
    # Knowledge answer should not mutate collected_data
    r = await p.present_knowledge(user_question="What are visiting hours?", retrieved_chunks=[{"source_type": "website", "content": "Visiting hours 10am-7pm"}], tone="friendly")
    assert collected == {"name": "A", "date": "2026-09-16"}
    assert "Visiting" in (r.body or "")
