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
    demand bolted onto a refusal reads as nagging. The trailing "Is there
    anything else I can help you with?" may be kept, so this asserts the
    parts that must be gone rather than exact equality.
    """
    screenshot = (
        "I don't have that information in our knowledge materials. "
        "Is there anything else I can help you with?"
    )
    out = _normalise_offtopic_decline(screenshot)

    assert "knowledge materials" not in out
    assert "verification code" not in out.lower()
    assert "resend" not in out.lower()
    assert "booking assistant" in out.lower()


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


@pytest.mark.parametrize(
    "reply, must_keep",
    [
        (
            "We offer Azure and Kentico development. Pricing is not mentioned in "
            "our materials, but I can help you book a consultation.",
            "Kentico",
        ),
        (
            "Our consulting fee is 500 rupees. Discounts are not covered in the "
            "documents I have.",
            "500",
        ),
        (
            "We do Kentico development across all versions. I do not have "
            "information about timelines.",
            "Kentico",
        ),
        (
            "We use .NET, C#, ASP.NET Core and Azure. Pricing is not in our "
            "knowledge base.",
            "ASP.NET",
        ),
    ],
)
def test_a_partial_answer_keeps_its_facts(reply: str, must_keep: str) -> None:
    """Regression: the decline matcher fired on a trailing caveat and threw
    away the real answer that preceded it.

    "We do Kentico development. I do not have information about timelines."
    was replaced wholesale with the decline, so a customer who asked about
    Kentico was told the assistant knew nothing about Kentico.
    """
    out = _normalise_offtopic_decline(reply)
    assert must_keep in out, (
        f"real content was discarded: expected {must_keep!r} in {out!r}"
    )
    # The kept half must not open by telling the customer it knows nothing,
    # which is the bug this guards: the reply must lead with the real content.
    assert not out.lower().startswith("i don't have")
    assert not out.lower().startswith("i do not have")


def test_a_pure_decline_is_still_replaced_wholesale() -> None:
    assert _normalise_offtopic_decline("I do not have that information.") == _OFFTOPIC_REPLY


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


# ── decline normalisation is topic-free: the MODEL mirrors topics ───────
# (prompt-instructed); no keyword code here names user topics.


def test_stacked_decline_is_deduped_to_one() -> None:
    """A reply already carrying a normalised decline (parroted from context)
    must not gain a second copy — one clean decline goes out."""
    blob = (
        "I don't have information about the president of India. I'm a "
        "booking assistant, so I can only help with appointments, I don't "
        "have information about that."
    )
    out = _normalise_offtopic_decline(blob)
    assert out.count("booking assistant") == 1
    assert not out.lstrip().lower().startswith("i don't have")


def test_generic_decline_counts_as_decline() -> None:
    """Strike/cooldown accounting keys off the boundary sentence."""
    from app.engine.offtopic_cooldown import looks_like_decline

    assert looks_like_decline(_OFFTOPIC_REPLY) is True


def test_generic_decline_leaks_no_scaffolding() -> None:
    lowered = _OFFTOPIC_REPLY.lower()
    for leaked in ("knowledgebase", "knowledge base", "materials", "documents"):
        assert leaked not in lowered
