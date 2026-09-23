"""Name-grounding guard: invented person names must never reach the user."""
from app.engine.knowledge_guards import find_ungrounded_names

CHUNKS = [
    "## Meet the Minds Behind DotStark\n"
    "Our leadership team brings deep expertise.\n"
    "Sunil Sharma\nCEO & Founder\nFounded DotStark with a vision.\n"
    "Yogendra Singh\nVP - Delivery & Client Success\n"
]


def test_invented_leadership_names_flagged():
    answer = (
        "Aditya Grover is part of the leadership team at DotStark, "
        "alongside Stefano Ermon and Volodymyr Kuleshov."
    )
    bad = find_ungrounded_names(answer, CHUNKS, safe_names=("DotStark",))
    assert "Aditya Grover" in bad
    assert "Stefano Ermon" in bad
    assert "Volodymyr Kuleshov" in bad


def test_real_names_from_chunks_pass():
    answer = "Sunil Sharma is the CEO & Founder of DotStark."
    assert find_ungrounded_names(answer, CHUNKS, safe_names=("DotStark",)) == []


def test_conversational_opener_not_flagged():
    answer = "Thanks Somya! DotStark was founded by Sunil Sharma."
    assert find_ungrounded_names(answer, CHUNKS, safe_names=("DotStark",)) == []


def test_boilerplate_and_titles_not_flagged():
    answer = "See About Us. Sunil Sharma, Chief Executive Officer, leads it."
    bad = find_ungrounded_names(answer, CHUNKS, safe_names=("DotStark",))
    assert bad == []


def test_honest_decline_passes():
    answer = "Sorry, I don't have that information in our records right now."
    assert find_ungrounded_names(answer, CHUNKS) == []


def test_single_capitalized_words_ignored():
    assert find_ungrounded_names("Confirm Monday please.", CHUNKS) == []
