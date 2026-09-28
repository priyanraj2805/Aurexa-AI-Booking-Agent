"""Off-topic questions must get one clean decline and no OTP nagging."""
import pytest

from app.engine.langgraph_orchestrator import (
    _OFFTOPIC_REPLY,
    _normalise_offtopic_decline,
)


# ── the exact messages from the screenshots ───────────────────────────

def test_the_screenshot_phrasings_are_normalised() -> None:
    """The bot produced a cold decline stapled to an OTP reminder.

    Both the wording and the reminder are wrong: "knowledge materials" is
    internal machinery the customer never heard of, and a verification-code
    demand bolted onto a refusal reads as nagging.
    """
    screenshot = (
        "I don't have that information in our knowledge materials. "
        "Is there anything else I can help you with?"
    )
    out = _normalise_offtopic_decline(screenshot)

    assert out == _OFFTOPIC_REPLY
    assert "knowledge materials" not in out
    assert "verification code" not in out.lower()
    assert "resend" not in out.lower()


@pytest.mark.parametrize(
    "cold",
    [
        "I don't have that information in our knowledge materials.",
        "The company knowledge materials don't include facts like capitals.",
        "I don't have information about that.",
        "Sorry, I don't have information about this.",
        "That is not available in our documents.",
        "I couldn't find that information.",
        "I'm unable to answer that.",
        "That isn't covered by the materials I have.",
    ],
)
def test_every_cold_decline_becomes_the_one_warm_reply(cold: str) -> None:
    assert _normalise_offtopic_decline(cold) == _OFFTOPIC_REPLY


def test_a_real_knowledge_answer_is_never_rewritten() -> None:
    """Only declines are normalised. Anything the knowledge base actually
    answered must reach the customer untouched, even if it says "we have
    information" or similar."""
    answer = "We offer Sitefinity, Umbraco and .NET development, priced per project."
    assert _normalise_offtopic_decline(answer) == answer

    priced = "Our consultation fee is 500 rupees, available Monday to Friday 9am-5pm."
    assert _normalise_offtopic_decline(priced) == priced


def test_empty_reply_falls_back_to_the_decline() -> None:
    assert _normalise_offtopic_decline("") == _OFFTOPIC_REPLY
    assert _normalise_offtopic_decline(None) == _OFFTOPIC_REPLY


def test_the_decline_never_leaks_internal_scaffolding() -> None:
    lowered = _OFFTOPIC_REPLY.lower()
    for leaked in ("knowledgebase", "knowledge base", "materials", "documents", "otp"):
        assert leaked not in lowered


def test_the_decline_names_what_the_assistant_can_do() -> None:
    lowered = _OFFTOPIC_REPLY.lower()
    assert "booking assistant" in lowered
    assert "appointment" in lowered
