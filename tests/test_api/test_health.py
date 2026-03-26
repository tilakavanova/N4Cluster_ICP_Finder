"""Tests for API health and basic endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport

from src.main import app


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_200(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "0.1.0"

    @pytest.mark.asyncio
    async def test_request_id_header(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert "X-Request-ID" in response.headers
        assert len(response.headers["X-Request-ID"]) == 8
