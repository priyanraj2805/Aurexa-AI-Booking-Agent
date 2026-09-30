"""Opening greeting: the customer's first message is their name.

Reported behaviour: a customer opened a new conversation with "Hi Yash" and
received

    "Hello! Could you please share your full name?"

The name they had just given was ignored and they were asked for it again.

The name is now captured, acknowledged by name, and the customer is moved on
to the next missing field. The salutation is built deterministically rather
than asked of the model, because a model asked to greet a named stranger
will sometimes greet them by the wrong name.
"""

from types import SimpleNamespace

import pytest

from app.engine.message_processor import (
    build_opening_greeting,
    extract_opening_name,
    is_opening_turn,
)


# â”€â”€ the exact reported case â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_reported_message_yields_the_name() -> None:
    assert extract_opening_name("Hi Yash") == "Yash"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Yash", "Yash"),
        ("Amanpreet Kaur", "Amanpreet Kaur"),
        ("Hi Yash", "Yash"),
        ("Hello Amanpreet", "Amanpreet"),
        ("hey amanpreet kaur", "amanpreet kaur"),
        ("Good morning Amanpreet", "Amanpreet"),
        ("Hello! Amanpreet.", "Amanpreet"),
        ("  Hi   Amanpreet   Kaur  ", "Amanpreet Kaur"),
    ],
)
def test_names_are_extracted(message: str, expected: str) -> None:
    assert extract_opening_name(message) == expected


# â”€â”€ must NOT be mistaken for a name â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@pytest.mark.parametrize(
    "message",
    [
        # greetings on their own carry no identity
        "hi",
        "hello",
        "hey there",
        "good morning",
        "namaste",
        # keysmash / gibberish
        "Ghhjjkk",
        "asdfgh",
        "qwertyuiop",
        "aaaa",
        "xyzzyx",
        # structured data belonging to a different field
        "amanpreet@dotstark.com",
        "8544322578",
        "+91 8544322578",
        "8oct",
        "Oct 6",
        "3:00 PM",
        # questions
        "What are your opening hours?",
        "can you help",
        "do you offer physiotherapy?",
        # too long to be a name
        "thisisareallyveryverylongsinglewordthatisnotaname",
        # too many words
        "one two three four five",
        # reserved field words
        "email",
        "phone",
        "appointment",
    ],
)
def test_non_names_are_rejected(message: str) -> None:
    assert extract_opening_name(message) == ""


@pytest.mark.parametrize("junk", [None, "", "   ", 12345, "...", "?!"])
def test_never_raises_on_junk(junk) -> None:
    assert extract_opening_name(junk) == ""


# â”€â”€ when is it an "opening"? â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _session(**collected):
    return SimpleNamespace(collected_data=collected)


def test_empty_session_is_the_opening_turn() -> None:
    assert is_opening_turn(_session()) is True
    assert is_opening_turn(_session(name="")) is True


@pytest.mark.parametrize("field", ["name", "email", "phone", "date", "time"])
def test_any_collected_field_ends_the_opening(field: str) -> None:
    """Any value at all means the conversation has started. Greeting a
    customer mid-booking would be wrong."""
    assert is_opening_turn(_session(**{field: "something"})) is False


def test_internal_keys_alone_do_not_count_as_started() -> None:
    assert is_opening_turn(_session(_internal={"x": 1}, whatsapp_chat_jid="a@b")) is True


# â”€â”€ the salutation itself â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_greeting_uses_the_name_and_the_business() -> None:
    text = build_opening_greeting("Yash", "Dotstark", " What email should we use?")
    assert "Hello Yash" in text
    assert "Dotstark" in text
    assert "What email should we use?" in text


def test_greeting_without_a_business_name_still_reads_naturally() -> None:
    text = build_opening_greeting("Yash", "", " How can we help?")
    assert "Hello Yash" in text
    assert "Thanks for reaching out." in text
    assert " ," not in text


def test_greeting_never_contains_a_double_space_or_starts_with_one() -> None:
    for name in ("Yash", "Amanpreet Kaur"):
        text = build_opening_greeting(name, "Dotstark", " What date?")
        assert not text.startswith(" ")
        assert "  " not in text


# -- the orchestrator hook must actually be wired up -----------------------
def test_orchestrator_calls_the_opening_greeting() -> None:
    """Guards a silent regression found during live testing: the hook was
    inserted into the orchestrator, but its field-definition import was
    wrong, the surrounding try/except swallowed the ImportError, and every
    customer was still asked for their name."""
    import inspect

    from app.engine import langgraph_orchestrator as orch

    source = inspect.getsource(orch)
    assert "maybe_greet_opening_name" in source, "hook call is gone"
    # The names must come from a module that actually exports them.
    for name in ("order_field_definitions", "parse_field_definitions"):
        assert f"    {name},\n" not in source, (
            f"{name} must be imported at module level, not from message_processor"
        )


@pytest.mark.parametrize(
    "junk",
    [
        # production: became the name on a real booking
        "Blaahahhahah",
        "Nahahhahahahz",
        "Babbzhahavq",
                "Gayhajvndkso",
    ],
)
def test_production_keysmash_is_rejected(junk: str) -> None:
    assert extract_opening_name(junk) == ""


@pytest.mark.parametrize(
    "name",
    ["Yash", "Amanpreet", "Amanpreet Kaur", "Ananya", "Mohammed", "Priyanshu Raj"],
)
def test_real_names_with_repeated_sounds_still_pass(name: str) -> None:
    assert extract_opening_name(name) == name

