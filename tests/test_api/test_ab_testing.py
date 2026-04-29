"""Tests for A/B testing API endpoints (NIF-238, NIF-262).

Covers:
- POST /ab-tests — create experiment
- GET /ab-tests — list experiments
- POST /ab-tests/{id}/start — start experiment
- GET /ab-tests/{id}/results — get results
- POST /ab-tests/{id}/declare-winner — declare winner
"""

import uuid
from unittest.mock import AsyncMock, patch, MagicMock

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


class TestCreateExperiment:
    @pytest.mark.asyncio
    async def test_create_experiment_success(self):
        mock_exp = MagicMock()
        mock_exp.id = uuid.uuid4()
        mock_exp.name = "Test Experiment"
        mock_exp.status = "draft"
        mock_exp.experiment_type = "template"

        with patch("src.api.routers.ab_testing.ABTestService") as MockSvc:
            instance = AsyncMock()
            instance.create_experiment.return_value = mock_exp
            MockSvc.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/v1/ab-tests", json={
                    "name": "Test Experiment",
                    "variants": [{"name": "A"}, {"name": "B"}],
                    "metric": "open_rate",
                    "sample_size": 50,
                })

            assert resp.status_code == 201
            data = resp.json()
            assert data["name"] == "Test Experiment"
            assert data["status"] == "draft"

    @pytest.mark.asyncio
    async def test_create_experiment_invalid_metric(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/ab-tests", json={
                "name": "Bad",
                "variants": [{"name": "A"}, {"name": "B"}],
                "metric": "invalid_metric",
                "sample_size": 50,
            })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_experiment_too_few_variants(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/ab-tests", json={
                "name": "One variant",
                "variants": [{"name": "A"}],
                "metric": "open_rate",
                "sample_size": 50,
            })
        assert resp.status_code == 422


class TestListExperiments:
    @pytest.mark.asyncio
    async def test_list_experiments(self):
        mock_exp = MagicMock()
        mock_exp.id = uuid.uuid4()
        mock_exp.name = "Exp 1"
        mock_exp.experiment_type = "template"
        mock_exp.status = "running"
        mock_exp.metric = "open_rate"
        mock_exp.sample_size = 100
        mock_exp.winner_variant = None
        mock_exp.created_at = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_exp]

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result

        from src.db.session import get_session

        async def override_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_session

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/ab-tests")

            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["name"] == "Exp 1"
        finally:
            app.dependency_overrides.pop(get_session, None)


class TestGetResults:
    @pytest.mark.asyncio
    async def test_get_results(self):
        mock_results = {
            "experiment_id": str(uuid.uuid4()),
            "name": "Test",
            "status": "running",
            "metric": "open_rate",
            "sample_size": 50,
            "winner_variant": None,
            "total_assigned": {"A": 25, "B": 25},
            "variant_stats": {
                "A": {"count": 20, "mean": 0.6, "stddev": 0.1, "ci_lower": 0.55, "ci_upper": 0.65},
                "B": {"count": 20, "mean": 0.4, "stddev": 0.1, "ci_lower": 0.35, "ci_upper": 0.45},
            },
        }

        with patch("src.api.routers.ab_testing.ABTestService") as MockSvc:
            instance = AsyncMock()
            instance.get_results.return_value = mock_results
            MockSvc.return_value = instance

            transport = ASGITransport(app=app)
            exp_id = uuid.uuid4()
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/api/v1/ab-tests/{exp_id}/results")

            assert resp.status_code == 200
            data = resp.json()
            assert "variant_stats" in data


class TestDeclareWinner:
    @pytest.mark.asyncio
    async def test_declare_winner(self):
        mock_result = {
            "experiment_id": str(uuid.uuid4()),
            "winner": "A",
            "p_value": 0.01,
            "significant": True,
            "best_variant": "A",
            "best_mean": 0.7,
            "second_variant": "B",
            "second_mean": 0.3,
        }

        with patch("src.api.routers.ab_testing.ABTestService") as MockSvc:
            instance = AsyncMock()
            instance.declare_winner.return_value = mock_result
            MockSvc.return_value = instance

            transport = ASGITransport(app=app)
            exp_id = uuid.uuid4()
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(f"/api/v1/ab-tests/{exp_id}/declare-winner")

            assert resp.status_code == 200
            data = resp.json()
            assert data["winner"] == "A"
            assert data["significant"] is True
