"""Multilingual RAG pipeline tests.

Regression tests for the bug where questions in non-Latin scripts (e.g.
Hindi) never retrieved knowledge: the keyword tokenizer was ASCII-only
and the LangGraph knowledge-question gate only recognised English
question tokens.
"""

from app.services.keyword_extractor import extract_keywords
from app.services.retrieval_service import references_uploaded_source
from app.engine.agent.graph import (
    _is_potential_knowledge_question,
    _looks_like_booking_field_value,
    _rag_tier,
    _should_skip_rag,
)


# ─── Keyword extraction ──────────────────────────────────────────────────


def test_keywords_english_unchanged():
    kws = extract_keywords("What are your prices?")
    assert "prices" in kws
    assert "what" not in kws  # stopword


def test_keywords_hindi_extracted():
    kws = extract_keywords("तुम्हारी कहानी किस बारे में है?")
    assert len(kws) > 0
    # Devanagari tokens survive extraction
    assert any(any(ord(ch) > 127 for ch in k) for k in kws)


def test_keywords_mixed_script():
    kws = extract_keywords("Tell me about भावुक chapter")
    assert "भावुक" in kws or any("भावुक" in k for k in kws)
    assert "chapter" in kws


def test_keywords_arabic():
    assert len(extract_keywords("ما هي أسعارك؟")) > 0


def test_keywords_drops_short_and_dupes():
    kws = extract_keywords("to be or not to be")
    assert kws == []


# ─── Knowledge-question gating (LangGraph path) ─────────────────────────


def test_gate_hindi_without_question_mark_is_knowledge():
    assert _is_potential_knowledge_question("अपनी स्टोरी के बारे में बताओ") is True
    assert _should_skip_rag("अपनी स्टोरी के बारे में बताओ") is False


def test_gate_english_question_still_knowledge():
    assert _is_potential_knowledge_question("What services do you offer?") is True
    assert _rag_tier("What services do you offer?") == "rerank"


def test_gate_booking_statement_gets_cheap_path_only():
    # Booking statements no longer starve RAG entirely: they run the cheap
    # BM25 lookup (no LLM rerank), which also surfaces policy pages during
    # e.g. cancellation requests.
    assert _should_skip_rag("I want to book an appointment") is False
    assert _rag_tier("I want to book an appointment") == "bm25_only"


def test_gate_confirmation_still_skipped():
    assert _looks_like_booking_field_value("yes sure thanks") is True
    assert _rag_tier("yes sure thanks") == "skip"


def test_gate_empty_message_skipped():
    assert _should_skip_rag("") is True
    assert _rag_tier("") == "skip"


# ─── Tiered RAG: bare topics / imperatives must not be starved ───────────


def test_tier_bare_noun_phrase_gets_bm25_only():
    # Regression: "the unspoken struggle" (a PDF title) previously skipped
    # RAG entirely, so the agent could never answer from the document.
    assert _rag_tier("the unspoken struggle") == "bm25_only"


def test_tier_imperative_summary_gets_rerank():
    assert _is_potential_knowledge_question("summary if this") is True
    assert _rag_tier("summary if this") == "rerank"
    assert (
        _rag_tier("okay give me the summary of 'the unspokern struggle'")
        == "rerank"
    )


def test_tier_field_values_still_skip():
    assert _rag_tier("john@example.com") == "skip"
    assert _rag_tier("9876543210") == "skip"
    assert _rag_tier("2026-08-24") == "skip"


# ─── Source-reference fallback ───────────────────────────────────────────


def test_references_uploaded_source_positive():
    assert references_uploaded_source("summarize the pdf I uploaded") is True
    assert references_uploaded_source("what is this book about?") is True
    assert references_uploaded_source("explain the document") is True


def test_references_uploaded_source_negative():
    assert references_uploaded_source("I want to book an appointment") is False
    assert references_uploaded_source("my email is john@test.com") is False
    assert references_uploaded_source("") is False
    assert references_uploaded_source(None) is False
