"""Tests for API versioning prefix /api/v1/ (NIF-258).

Covers:
- /health returns 200 at root (no version prefix)
- /api/v1/leads returns 200 (versioned)
- /leads (without prefix) returns 404
- /api/v1/restaurants returns 200 (versioned)
- /restaurants (without prefix) returns 404
- /t/ tracking endpoint works at root (not under /api/v1/)
- /webhooks/ works at root (not under /api/v1/)
- /unsubscribe/ works at root (not under /api/v1/)
"""

import pytest
from httpx import AsyncClient, ASGITransport

from src.main import app


# All API requests use dev-mode auth (no API_KEY configured in tests)


class TestVersionedEndpoints:
    @pytest.mark.asyncio
    async def test_health_at_root(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/health")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_leads_versioned_path(self):
        """GET /api/v1/leads should return 200 (or 401 in auth mode, not 404)."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/v1/leads")
        # Dev mode: 200.  Auth-required mode: 401.  Not-found: 404.
        assert r.status_code != 404

    @pytest.mark.asyncio
    async def test_leads_unversioned_returns_404(self):
        """GET /leads (no /api/v1 prefix) must return 404."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/leads")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_restaurants_versioned_path(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/v1/restaurants")
        assert r.status_code != 404

    @pytest.mark.asyncio
    async def test_restaurants_unversioned_returns_404(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/restaurants")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_scores_versioned_path(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/v1/scores")
        assert r.status_code != 404

    @pytest.mark.asyncio
    async def test_scores_unversioned_returns_404(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/scores")
        assert r.status_code == 404


class TestRootLevelEndpoints:
    """Tracking, webhook, and unsubscribe endpoints must stay at root."""

    @pytest.mark.asyncio
    async def test_tracking_pixel_at_root(self):
        """GET /px/{token}.gif must be reachable (not 404) — even if token is invalid."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/px/fakeshorttok.gif")
        # 404 would mean the route doesn't exist; any other code means route is registered
        assert r.status_code != 404

    @pytest.mark.asyncio
    async def test_click_redirect_at_root(self):
        """GET /t/{token} must be reachable (not 404)."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/t/fakeshorttok")
        assert r.status_code != 404

    @pytest.mark.asyncio
    async def test_unsubscribe_at_root(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/unsubscribe/faketoken")
        assert r.status_code != 404

    @pytest.mark.asyncio
    async def test_webhook_not_under_v1(self):
        """POST /api/v1/webhooks must be 404 — webhooks live at /webhooks/."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/webhooks/sendgrid", json={})
        assert r.status_code == 404
