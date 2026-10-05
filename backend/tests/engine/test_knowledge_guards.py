"""Knowledge grounding guards — relevance gating, answer verification,
entity blocking. All deterministic, all on live data (no hardcoded KB)."""
from app.engine import knowledge_guards as guards


def test_gate_keeps_overlapping_chunks():
    chunks = [
        {"content": "Our clinic opens daily from 9 AM to 6 PM for consultations."},
        {"content": "The football match starts at midnight under floodlights."},
    ]
    kept, dropped_all = guards.gate_chunks_by_relevance(
        chunks, "what are your clinic hours?"
    )
    assert len(kept) == 1
    assert "clinic" in kept[0]["content"]
    assert dropped_all is False


def test_gate_drops_everything_unrelated():
    chunks = [
        {"content": "The football match starts at midnight under floodlights."},
    ]
    kept, dropped_all = guards.gate_chunks_by_relevance(
        chunks, "what are your clinic consultation fees?"
    )
    assert kept == []
    assert dropped_all is True


def test_gate_respects_rerank_score():
    chunks = [{"content": "Totally unrelated words here.", "rerank_score": 8}]
    kept, dropped_all = guards.gate_chunks_by_relevance(chunks, "clinic hours?")
    assert len(kept) == 1
    assert dropped_all is False


def test_gate_empty_input():
    kept, dropped_all = guards.gate_chunks_by_relevance([], "anything?")
    assert kept == []
    assert dropped_all is False


def test_verify_grounded_answer_passes():
    chunks = [{"content": "Our clinic opens daily from 9 AM to 6 PM."}]
    ok, reason = guards.verify_knowledge_answer(
        "We are open daily from 9 AM to 6 PM.", chunks
    )
    assert ok is True
    assert reason == "grounded"


def test_verify_honest_decline_passes():
    ok, reason = guards.verify_knowledge_answer(
        "Sorry, I don't have that information right now.", []
    )
    assert ok is True
    assert reason == "decline"


def test_verify_ungrounded_answer_fails():
    chunks = [{"content": "Our clinic opens daily from 9 AM to 6 PM."}]
    ok, reason = guards.verify_knowledge_answer(
        "Our head surgeon Dr. Invented charges premium rates on Mars.", chunks
    )
    assert ok is False
    assert reason == "ungrounded"


def test_unlisted_phone_blocked_but_listed_passes():
    facts = ["Call our clinic at +91 98765 43210 for appointments."]
    assert guards.find_unlisted_entities(
        "Call us at +91 98765 43210.", facts
    ) == []
    blocked = guards.find_unlisted_entities("Call us at +91 11111 22222.", facts)
    assert blocked == ["+91 11111 22222"]


def test_unlisted_price_blocked_but_customer_echo_passes():
    facts = ["Consultation fee is INR 500."]
    assert guards.find_unlisted_entities("Your fee is INR 500.", facts) == []
    blocked = guards.find_unlisted_entities(
        "That will be Rs 5000 payable now.", facts
    )
    assert len(blocked) == 1
    assert "5000" in blocked[0]


def test_guard_final_text_pass_through_and_block():
    body = "Hello! How can I help you today?"
    safe, blocked, ents = guards.guard_final_text(
        body, org_facts=["Acme Clinic"], session_details=[]
    )
    assert blocked is False
    assert safe == body
    assert ents == []

    body2 = "Sure, call Dr. X at +91 11111 22222 to confirm."
    safe2, blocked2, ents2 = guards.guard_final_text(
        body2, org_facts=["Acme Clinic"], session_details=[]
    )
    assert blocked2 is True
    assert ents2 == ["+91 11111 22222"]
    # The invented number is masked in place. The reply is no longer discarded
    # in favour of a canned "I don't have that information" apology.
    assert "+91 11111 22222" not in safe2
    assert "[hidden]" in safe2
    assert "don't have that information" not in safe2.lower()


def test_guard_final_text_payment_skips_price():
    body = "Please pay INR 500 via this Razorpay order link."
    safe, blocked, _ = guards.guard_final_text(
        body, org_facts=["Acme Clinic"], session_details=[]
    )
    assert blocked is False
    assert safe == body


def test_no_hardcoded_business_content_in_module():
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "app" / "engine" / "knowledge_guards.py"
    text = src.read_text(encoding="utf-8")
    for literal in ("98765", "reediff", "Dr. ", "Rs 500", "9 AM to 6 PM"):
        assert literal not in text, f"hardcoded business content leaked: {literal}"
    # "example.com" is deliberately NOT in the forbidden list: RFC 2606
    # reserves example.com/org/net as documentation domains that can never
    # be a real address, and _PLACEHOLDER_EMAIL_DOMAINS uses exactly that
    # set so illustrative examples ("like name@example.com") are never
    # masked as [hidden]. Pinned by test_placeholder_and_repeat_guard.py.


def test_distinctive_term_logic_physio_vs_azure() -> None:
    """The production failure: "do you offer physiotherapy?" kept Azure
    chunks via the generic verb "offer". A distinctive query word (6+ chars)
    must appear in the kept material."""
    from app.engine.knowledge_guards import content_words

    query_words = content_words("do you offer physiotherapy?")
    distinctive = {w for w in query_words if len(w) >= 6}
    assert "physiotherapy" in distinctive
    azure = content_words(
        "Dotstark offers Azure development and Kentico content migration services."
    )
    assert not (distinctive & azure)


def test_distinctive_term_logic_hours_question_passes() -> None:
    """A legitimate question must keep its chunks: "opening" appears in the
    hours material."""
    from app.engine.knowledge_guards import content_words

    query_words = content_words("what are your opening hours?")
    distinctive = {w for w in query_words if len(w) >= 6}
    assert "opening" in distinctive
    hours = content_words("Our hours are Monday to Friday 9 to 5. Opening at 9.")
    assert distinctive & hours
