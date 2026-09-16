import pytest


def _presenter_intr(text, options=None, dtype="SELECT_DATE", did="d1"):
    from app.engine.whatsapp_presenter import StructuredWhatsAppResponse

    return StructuredWhatsAppResponse(
        type="directive",
        intro=text,
        outro="Just reply with the number." if options else None,
        directive_type=dtype,
        directive_id=did,
        options=options or [],
    )


def test_render_text():
    from app.core.whatsapp_renderer import render_structured

    r = render_structured({"type": "text", "body": "Hello world"}, transport="baileys")
    assert r["type"] == "text" and "Hello world" in r["body"]


def test_render_directive_baileys_buttons():
    from app.core.whatsapp_renderer import render_structured

    opts = [{"id": "date_20260916", "label": "Wed, Sep 16", "value": "2026-09-16"}, {"id": "date_20260917", "label": "Thu, Sep 17", "value": "2026-09-17"}]
    s = _presenter_intr("Pick a date 😊", opts, "SELECT_DATE")
    r = render_structured(s, transport="baileys")
    assert r["type"] == "interactive_buttons"
    assert "Pick a date" in r["body"]
    # IDs preserved
    assert r["buttons"][0]["id"] == "date_20260916"


def test_render_directive_baileys_list():
    from app.core.whatsapp_renderer import render_structured

    opts = [{"id": f"date_202609{i:02d}", "label": f"Sep {i}", "value": f"2026-09-{i:02d}"} for i in range(10, 17)]
    s = _presenter_intr("Choose your preferred date", opts, "SELECT_DATE")
    r = render_structured(s, transport="baileys")
    assert r["type"] == "interactive_list"
    assert len(r["sections"][0]["rows"]) == 7


def test_render_directive_cloud_text_fallback():
    from app.core.whatsapp_renderer import render_structured

    opts = [{"id": "slot_20260916_0900", "label": "9:00 AM", "value": "09:00:00"}, {"id": "slot_20260916_1000", "label": "10:00 AM", "value": "10:00:00"}]
    s = _presenter_intr("Available times today", opts, "SELECT_SLOT")
    r = render_structured(s, transport="cloud")
    assert r["type"] == "text"
    assert "1. 9:00 AM" in r["body"] and "2. 10:00 AM" in r["body"]
    assert "Reply with the option number" in r["body"]


def test_options_ids_preserved():
    from app.core.whatsapp_renderer import render_structured

    opts = [{"id": "date_20260916", "label": "Wed, Sep 16", "value": "2026-09-16"}]
    s = _presenter_intr("Pick one", opts, "SELECT_DATE", did="abc123")
    r = render_structured(s, transport="baileys")
    assert r["buttons"][0]["id"] == "date_20260916"


def test_no_llm_required():
    from app.core.whatsapp_renderer import render_structured
    from app.engine.whatsapp_presenter import StructuredWhatsAppResponse

    # Direct structured without LLM
    s = StructuredWhatsAppResponse(type="text", body="Hi there")
    r = render_structured(s, transport="cloud")
    assert "Hi there" in r["body"]
