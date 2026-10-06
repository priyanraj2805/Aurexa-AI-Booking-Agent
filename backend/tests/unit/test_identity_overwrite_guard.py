"""Identity overwrite guard: a hallucinated phone/email must never replace
a collected value on a turn that is not providing contact details.

Production: the customer's +91 number was overwritten by a hallucinated
+212 number on a "3 works for me" turn — the OTP went to a number that
does not exist and the customer could never verify.
"""

from app.engine.agent.graph import _blocked_identity_overwrites


def _collected():
    return {
        "name": "Nishant Raj",
        "email": "nishantraj785908@gmail.com",
        "phone": "+917859084667",
        "date": "2026-10-06",
        "time": "10:30 AM",
    }


def test_hallucinated_phone_blocked_when_turn_carries_no_digits():
    blocked = _blocked_identity_overwrites(
        collected=_collected(),
        validated_extracted={"phone": "+212639649815"},
        missing=["time"],
        reopen_fields=[],
        user_message="3 works for me",
    )
    assert blocked == ["phone"]


def test_hallucinated_email_blocked_when_turn_carries_no_email():
    blocked = _blocked_identity_overwrites(
        collected=_collected(),
        validated_extracted={"email": "someone.else@example.com"},
        missing=["time"],
        reopen_fields=[],
        user_message="3 works for me",
    )
    assert blocked == ["email"]


def test_explicit_correction_with_digits_is_allowed():
    blocked = _blocked_identity_overwrites(
        collected=_collected(),
        validated_extracted={"phone": "+919822047136"},
        missing=["time"],
        reopen_fields=[],
        user_message="It is 9822047136",
    )
    assert blocked == []


def test_explicit_correction_with_email_is_allowed():
    blocked = _blocked_identity_overwrites(
        collected=_collected(),
        validated_extracted={"email": "new.address@example.com"},
        missing=["time"],
        reopen_fields=[],
        user_message="actually use new.address@example.com",
    )
    assert blocked == []


def test_reopened_field_is_allowed():
    blocked = _blocked_identity_overwrites(
        collected=_collected(),
        validated_extracted={"phone": "+212639649815"},
        missing=["time"],
        reopen_fields=["phone"],
        user_message="change my number",
    )
    assert blocked == []


def test_missing_field_is_allowed():
    blocked = _blocked_identity_overwrites(
        collected={"name": "Nishant Raj"},
        validated_extracted={"phone": "+917859084667"},
        missing=["email", "phone", "date", "time"],
        reopen_fields=[],
        user_message="Ok 7859084667",
    )
    assert blocked == []


def test_same_value_is_never_blocked():
    blocked = _blocked_identity_overwrites(
        collected=_collected(),
        validated_extracted={"phone": "+917859084667"},
        missing=["time"],
        reopen_fields=[],
        user_message="yes",
    )
    assert blocked == []
