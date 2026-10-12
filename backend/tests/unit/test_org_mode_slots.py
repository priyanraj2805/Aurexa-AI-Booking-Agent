"""Phase 3+4: provider slots + chat virtual id."""

import sys
from datetime import datetime, timezone

sys.path.insert(0, "backend")

from app.schemas.chat import ChatMessageRequest


def test_virtual_slot_id_org_format():
    r = ChatMessageRequest(
        message="ok",
        selected_slot_id="virtual|11111111-1111-1111-1111-111111111111|2026-10-07T10:00:00+00:00",
    )
    assert r.selected_slot_id.startswith("virtual|")


def test_virtual_slot_id_provider_format():
    r = ChatMessageRequest(
        message="ok",
        selected_slot_id=(
            "virtual|11111111-1111-1111-1111-111111111111"
            "|22222222-2222-2222-2222-222222222222"
            "|2026-10-07T10:00:00+00:00"
        ),
        unit_id="33333333-3333-3333-3333-333333333333",
        provider_id="22222222-2222-2222-2222-222222222222",
    )
    assert r.provider_id is not None
    assert r.unit_id is not None


def test_chat_request_individual_backward_compat():
    r = ChatMessageRequest(message="hi")
    assert r.unit_id is None
    assert r.provider_id is None
    assert r.selected_slot_id is None
