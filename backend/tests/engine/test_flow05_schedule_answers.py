import json
import uuid
from datetime import datetime, time, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from app.engine.agent.graph import call_model
from app.engine.scheduling_policy import (
    build_schedule_business_hours_answer,
    lookup_schedule_business_hours_answer,
)


def _base_weekly_schedule():
    return [
        {"day_of_week": 0, "is_active": True, "open_time": "09:00:00", "close_time": "17:00:00"},
        {"day_of_week": 1, "is_active": True, "open_time": "09:00:00", "close_time": "17:00:00"},
        {"day_of_week": 2, "is_active": True, "open_time": "09:00:00", "close_time": "17:00:00"},
        {"day_of_week": 3, "is_active": True, "open_time": "09:00:00", "close_time": "17:00:00"},
        {"day_of_week": 4, "is_active": True, "open_time": "09:00:00", "close_time": "17:00:00"},
        {"day_of_week": 5, "is_active": False, "open_time": "09:00:00", "close_time": "17:00:00"},
        {"day_of_week": 6, "is_active": False, "open_time": "09:00:00", "close_time": "17:00:00"},
    ]


def test_flow05_regular_open_day_hours_are_policy_derived():
    answer = build_schedule_business_hours_answer(
        user_message="What are your business hours on 2026-06-01?",
        effective_timezone="America/New_York",
        schedule_timezone="America/New_York",
        weekly_schedule=_base_weekly_schedule(),
        schedule_exceptions=[],
        reference_utc=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
    )

    assert answer is not None
    assert answer["response_text"] == "Our hours on Monday (2026-06-01) are 9:00 AM to 5:00 PM (America/New_York)."
    assert answer["status"] == "open"
    assert answer["source"] == "policy"


def test_flow05_closed_day_response_is_policy_correct():
    answer = build_schedule_business_hours_answer(
        user_message="Are you open on 2026-06-07?",
        effective_timezone="America/New_York",
        schedule_timezone="America/New_York",
        weekly_schedule=_base_weekly_schedule(),
        schedule_exceptions=[],
        reference_utc=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
    )

    assert answer is not None
    assert answer["response_text"] == "We're closed on Sunday (2026-06-07) in America/New_York."
    assert answer["status"] == "closed"
    assert answer["source"] == "policy"


def test_flow05_exception_custom_hours_override_regular_hours():
    answer = build_schedule_business_hours_answer(
        user_message="What are your hours on 2026-06-03?",
        effective_timezone="America/New_York",
        schedule_timezone="America/New_York",
        weekly_schedule=_base_weekly_schedule(),
        schedule_exceptions=[
            {
                "exception_date": "2026-06-03",
                "exception_type": "CUSTOM_HOURS",
                "custom_open": "12:00:00",
                "custom_close": "15:00:00",
            }
        ],
        reference_utc=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
    )

    assert answer is not None
    assert answer["response_text"] == "Our hours on Wednesday (2026-06-03) are 12:00 PM to 3:00 PM (America/New_York)."
    assert answer["status"] == "open_exception"
    assert answer["source"] == "policy"


def test_flow05_rejects_fabricated_hours_in_user_prompt():
    answer = build_schedule_business_hours_answer(
        user_message="Are you open 24/7 on 2026-06-07?",
        effective_timezone="America/New_York",
        schedule_timezone="America/New_York",
        weekly_schedule=_base_weekly_schedule(),
        schedule_exceptions=[],
        reference_utc=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
    )

    assert answer is not None
    assert "24/7" not in answer["response_text"]
    assert answer["response_text"] == "We're closed on Sunday (2026-06-07) in America/New_York."


@pytest.mark.asyncio
async def test_flow05_graph_routes_schedule_question_to_policy_first_without_llm(monkeypatch):
    monkeypatch.setattr(
        "app.engine.agent.graph.AgentContextBuilder.get_field_definitions",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.engine.agent.graph.lookup_schedule_business_hours_answer",
        AsyncMock(
            return_value={
                "response_text": "Our hours on Monday (2026-06-01) are 9:00 AM to 5:00 PM (America/New_York).",
                "status": "open",
                "source": "policy",
                "effective_timezone": "America/New_York",
            }
        ),
    )

    llm_mock = AsyncMock(side_effect=AssertionError("LLM should not be called for schedule policy answers"))
    monkeypatch.setattr("app.engine.agent.graph.llm", SimpleNamespace(ainvoke=llm_mock))

    result = await call_model(
        {
            "messages": [HumanMessage(content="What are your hours on 2026-06-01?")],
            "summary": "",
            "session_id": "session-flow05-policy",
            "org_id": "org-1",
            "org_config": {"timezone": "America/New_York"},
            "collected_fields": {},
            "current_intent": "ASK_QUESTION",
            "otp_verified": False,
            "user_timezone": "America/New_York",
            "optional_skips": [],
            "pending_conflict": {},
            "follow_up_fields": [],
            "captured_deltas": {},
            "merge_diagnostics": {},
        },
        config={"configurable": {"org_config": {"timezone": "America/New_York"}, "db": SimpleNamespace()}},
    )

    assert llm_mock.await_count == 0
    payload = json.loads(result["messages"][-1].content)
    assert payload["response_text"] == "Our hours on Monday (2026-06-01) are 9:00 AM to 5:00 PM (America/New_York)."
    assert result["schedule_answer"]["source"] == "policy"


@pytest.mark.asyncio
async def test_generic_hours_question_answered_mid_booking_flow(db_session):
    """Production: "What are your working hours?" mid-booking (name already
    collected) fell through to RAG and the customer got "I don't have
    information about that" for hours we configure. The weekly summary
    must answer with booking progress untouched."""
    from app.models.booking import BusinessSchedule, ScheduleDay

    org_id = uuid.uuid4()
    db_session.add(
        BusinessSchedule(
            org_id=org_id,
            slot_duration_minutes=60,
            booking_window_days=60,
            timezone="UTC",
        )
    )
    for dow in range(7):
        db_session.add(
            ScheduleDay(
                org_id=org_id,
                day_of_week=dow,
                is_active=dow < 5,
                open_time=time(9, 0),
                close_time=time(17, 0),
            )
        )
    await db_session.commit()

    answer = await lookup_schedule_business_hours_answer(
        db=db_session,
        org_id=str(org_id),
        user_message="What are your working hours?",
        effective_timezone="UTC",
        org_timezone="UTC",
        in_booking_flow=True,
    )
    assert answer is not None
    assert answer["status"] == "weekly_hours"
    assert "Monday" in answer["response_text"]
    assert "Sunday" in answer["response_text"] or "closed" in answer["response_text"].lower()
