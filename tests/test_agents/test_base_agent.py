"""Tests for the base agent framework (NIF-269).

Covers:
- AgentResult serialisation
- BaseAgent.execute tracking (success and failure)
- Agent registry (register, get, list)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base import (
    AgentResult,
    BaseAgent,
    register_agent,
    get_agent,
    list_agents,
    _registry,
)


class DummyAgent(BaseAgent):
    name = "dummy"
    description = "A dummy agent for testing"

    async def run(self, context, session=None):
        if context.get("fail"):
            raise ValueError("Forced failure")
        return AgentResult(success=True, data={"echo": context})


class TestAgentResult:
    def test_to_dict(self):
        result = AgentResult(success=True, data={"key": "val"}, errors=[], metadata={"v": 1})
        d = result.to_dict()
        assert d["success"] is True
        assert d["data"]["key"] == "val"
        assert d["metadata"]["v"] == 1

    def test_default_values(self):
        result = AgentResult(success=False)
        d = result.to_dict()
        assert d["data"] == {}
        assert d["errors"] == []


class TestBaseAgentExecute:
    @pytest.mark.asyncio
    async def test_execute_success_no_session(self):
        """Execute without a DB session still works."""
        agent = DummyAgent()
        result = await agent.execute({"hello": "world"})
        assert result.success is True
        assert result.data["echo"]["hello"] == "world"

    @pytest.mark.asyncio
    async def test_execute_failure(self):
        """Execute catches exceptions and returns failure result."""
        agent = DummyAgent()
        result = await agent.execute({"fail": True})
        assert result.success is False
        assert "Forced failure" in result.errors[0]

    @pytest.mark.asyncio
    async def test_execute_with_session_tracks_run(self):
        """Execute with a session creates an AgentRun record."""
        agent = DummyAgent()
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        result = await agent.execute({"test": True}, session)
        assert result.success is True
        # AgentRun was added to session
        session.add.assert_called()

    @pytest.mark.asyncio
    async def test_execute_with_session_tracks_failure(self):
        """Execute with session logs failure in AgentRun."""
        agent = DummyAgent()
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        result = await agent.execute({"fail": True}, session)
        assert result.success is False
        # Still added the run record
        session.add.assert_called()


class TestAgentRegistry:
    def setup_method(self):
        # Save and clear registry for test isolation
        self._saved = dict(_registry)
        _registry.clear()

    def teardown_method(self):
        _registry.clear()
        _registry.update(self._saved)

    def test_register_and_get(self):
        agent = DummyAgent()
        register_agent(agent)
        assert get_agent("dummy") is agent

    def test_get_missing(self):
        assert get_agent("nonexistent") is None

    def test_list_agents(self):
        register_agent(DummyAgent())
        agents = list_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "dummy"
        assert agents[0]["description"] == "A dummy agent for testing"
