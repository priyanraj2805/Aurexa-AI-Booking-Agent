"""Tests for agent core."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.engine.agent.core import get_booking_agent, run_booking_agent
from app.engine.agent.schemas import BookingResponse, UserIntent


class TestGetBookingAgent:
    def test_returns_agent(self):
        agent = get_booking_agent()
        assert agent is not None

    def test_singleton_pattern(self):
        agent1 = get_booking_agent()
        agent2 = get_booking_agent()
        assert agent1 is agent2


class TestRunBookingAgent:
    @pytest.mark.asyncio
    async def test_returns_booking_response_structure(self):
        # This is an integration test shape - actual LLM mocking
        # would be added in integration tests
        pass
