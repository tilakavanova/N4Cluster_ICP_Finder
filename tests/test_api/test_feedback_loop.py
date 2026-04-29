"""Tests for conversion feedback loop API endpoints (NIF-260).

Covers:
- GET /feedback/report returns combined report
- POST /feedback/suggest-adjustments returns suggestions
- POST /feedback/apply applies weight changes
- POST /feedback/apply returns 404 for missing profile
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.auth import require_auth
from src.main import app


def _dev_auth_override():
    return {"mode": "dev", "sub": "dev-mode", "scopes": ["admin:all"]}


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[require_auth] = _dev_auth_override
    yield
    app.dependency_overrides.pop(require_auth, None)


class TestFeedbackReport:
    @pytest.mark.asyncio
    async def test_returns_report(self):
        mock_report = {
            "period": "2026-04",
            "overall_conversion_rate": 33.33,
            "total_discovered": 18,
            "total_converted": 6,
            "score_bucket_analysis": [],
            "signal_analysis": [],
        }

        with patch("src.api.routers.feedback_loop.get_feedback_report", new_callable=AsyncMock, return_value=mock_report):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/feedback/report?period=2026-04")

            assert resp.status_code == 200
            data = resp.json()
            assert data["period"] == "2026-04"
            assert data["overall_conversion_rate"] == 33.33


class TestSuggestAdjustments:
    @pytest.mark.asyncio
    async def test_returns_suggestions(self):
        mock_result = {
            "period": "2026-04",
            "profile_id": "pid-123",
            "adjustments": [
                {"signal": "independent", "suggested_adjustment": 2.0},
            ],
        }

        with patch("src.api.routers.feedback_loop.suggest_weight_adjustments", new_callable=AsyncMock, return_value=mock_result):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/feedback/suggest-adjustments",
                    json={"period": "2026-04"},
                )

            assert resp.status_code == 200
            data = resp.json()
            assert len(data["adjustments"]) == 1


class TestApplyAdjustments:
    @pytest.mark.asyncio
    async def test_applies_adjustments(self):
        profile_id = str(uuid.uuid4())
        mock_result = {
            "profile_id": profile_id,
            "new_version": 2,
            "changes": {"independent": {"old_weight": 15.0, "new_weight": 18.0}},
        }

        with patch("src.api.routers.feedback_loop.apply_adjustments", new_callable=AsyncMock, return_value=mock_result):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/feedback/apply",
                    json={
                        "profile_id": profile_id,
                        "adjustments": [{"signal": "independent", "new_weight": 18.0}],
                        "approved_by": "admin@test.com",
                    },
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["new_version"] == 2

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_profile(self):
        profile_id = str(uuid.uuid4())
        mock_result = {"error": "profile_not_found"}

        with patch("src.api.routers.feedback_loop.apply_adjustments", new_callable=AsyncMock, return_value=mock_result):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/feedback/apply",
                    json={
                        "profile_id": profile_id,
                        "adjustments": [{"signal": "independent", "new_weight": 18.0}],
                    },
                )

            assert resp.status_code == 404
