"""Tests for concrete agent implementations (NIF-269-273).

Covers:
- LeadDiscoveryAgent returns results without DB
- QualificationAgent requires restaurant_id
- OutreachAgent channel selection
- ClosingAgent follow-up planning
- CoordinatorAgent status reporting
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.lead_discovery import LeadDiscoveryAgent
from src.agents.qualification import QualificationAgent
from src.agents.outreach import OutreachAgent, _select_channel
from src.agents.closing import ClosingAgent
from src.agents.coordinator import CoordinatorAgent


class TestLeadDiscoveryAgent:
    @pytest.mark.asyncio
    async def test_requires_zip_codes(self):
        agent = LeadDiscoveryAgent()
        result = await agent.run({})
        assert result.success is False
        assert "zip_codes required" in result.errors[0]

    @pytest.mark.asyncio
    async def test_returns_empty_without_session(self):
        agent = LeadDiscoveryAgent()
        result = await agent.run({"zip_codes": ["10001"]})
        assert result.success is True
        assert result.data["leads_found"] == 0

    @pytest.mark.asyncio
    async def test_filters_in_data(self):
        agent = LeadDiscoveryAgent()
        result = await agent.run({"zip_codes": ["10001"], "cuisines": ["Pizza"]})
        assert result.data["filters"]["cuisines"] == ["Pizza"]


class TestQualificationAgent:
    @pytest.mark.asyncio
    async def test_requires_restaurant_id(self):
        agent = QualificationAgent()
        result = await agent.run({})
        assert result.success is False
        assert "restaurant_id required" in result.errors[0]

    @pytest.mark.asyncio
    async def test_requires_session(self):
        agent = QualificationAgent()
        result = await agent.run({"restaurant_id": "abc-123"})
        assert result.success is False
        assert "Database session required" in result.errors[0]


class TestOutreachAgent:
    @pytest.mark.asyncio
    async def test_requires_lead_or_restaurant(self):
        agent = OutreachAgent()
        result = await agent.run({})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_channel_recommendation(self):
        agent = OutreachAgent()
        result = await agent.run({"lead_id": "abc", "email": "test@example.com"})
        assert result.success is True
        assert "recommended_channel" in result.data

    def test_channel_selection_phone(self):
        assert _select_channel({"phone": "+1555", "business_type": "restaurant"}) == "sms"

    def test_channel_selection_email(self):
        assert _select_channel({"email": "a@b.com"}) == "email"

    def test_channel_selection_fallback(self):
        assert _select_channel({}) == "call"


class TestClosingAgent:
    @pytest.mark.asyncio
    async def test_requires_lead_id(self):
        agent = ClosingAgent()
        result = await agent.run({})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_plan_followups(self):
        agent = ClosingAgent()
        result = await agent.run({"lead_id": "xyz", "action": "plan_followups"})
        assert result.success is True
        assert result.data["total_steps"] == 5
        assert len(result.data["sequence"]) == 5

    @pytest.mark.asyncio
    async def test_schedule_demo(self):
        agent = ClosingAgent()
        result = await agent.run({"lead_id": "xyz", "action": "schedule_demo"})
        assert result.success is True
        assert result.data["action"] == "demo_scheduled"

    @pytest.mark.asyncio
    async def test_handoff_requires_rep_id(self):
        agent = ClosingAgent()
        result = await agent.run({"lead_id": "xyz", "action": "handoff"})
        assert result.success is False
        assert "rep_id required" in result.errors[0]


class TestCoordinatorAgent:
    @pytest.mark.asyncio
    async def test_get_status(self):
        agent = CoordinatorAgent()
        result = await agent.run({"action": "get_status"})
        assert result.success is True
        assert "pipeline_stages" in result.data
        assert len(result.data["pipeline_stages"]) == 4

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        agent = CoordinatorAgent()
        result = await agent.run({"action": "invalid"})
        assert result.success is False
