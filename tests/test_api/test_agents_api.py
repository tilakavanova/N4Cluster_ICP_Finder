"""Tests for AI Agents API router (NIF-269-274).

Covers:
- GET /agents lists registered agents
- POST /agents/{name}/run with valid agent
- POST /agents/{name}/run with invalid agent returns 404
- POST /agents/{name}/feedback validates input
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app


from src.api.auth import require_auth


def _bypass_auth():
    return {"mode": "test", "sub": "test-user", "scopes": ["admin:all"]}


@pytest.fixture
def client():
    app.dependency_overrides[require_auth] = _bypass_auth
    yield TestClient(app)
    app.dependency_overrides.pop(require_auth, None)


@pytest.fixture(autouse=True)
def skip_auth():
    """No-op — auth bypass is now handled via FastAPI dependency_overrides in client fixture."""
    yield


class TestListAgents:
    def test_list_agents(self, client):
        resp = client.get("/api/v1/agents", headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        # At least the 5 registered agents + coordinator
        names = {a["name"] for a in data["agents"]}
        assert "lead_discovery" in names
        assert "qualification" in names
        assert "outreach" in names
        assert "closing" in names
        assert "coordinator" in names


class TestRunAgent:
    def test_run_unknown_agent_404(self, client):
        resp = client.post(
            "/api/v1/agents/nonexistent/run",
            json={"context": {}},
            headers={"X-API-Key": "test"},
        )
        assert resp.status_code == 404

    def test_run_closing_agent(self, client):
        """ClosingAgent plan_followups works via API."""
        from src.db.session import get_session

        async def mock_get_session():
            mock_session = AsyncMock()
            mock_session.commit = AsyncMock()
            mock_session.add = MagicMock()
            mock_session.flush = AsyncMock()
            yield mock_session

        app.dependency_overrides[get_session] = mock_get_session
        try:
            resp = client.post(
                "/api/v1/agents/closing/run",
                json={"context": {"lead_id": "abc", "action": "plan_followups"}},
                headers={"X-API-Key": "test"},
            )
        finally:
            app.dependency_overrides.pop(get_session, None)
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "closing"


class TestAgentFeedback:
    def test_feedback_unknown_agent_404(self, client):
        resp = client.post(
            "/api/v1/agents/nonexistent/feedback",
            json={"rating": 3, "rated_by": "tester"},
            headers={"X-API-Key": "test"},
        )
        assert resp.status_code == 404
