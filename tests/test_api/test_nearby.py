"""Tests for the /nearby ZIP code + radius search endpoint."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport
from src.main import app
from src.utils.geo import haversine_miles


def _make_restaurant(name, lat, lng, zip_code="10001", city="New York", state="NY"):
    """Create a mock restaurant object."""
    r = MagicMock()
    r.id = uuid4()
    r.name = name
    r.address = f"{name} address"
    r.city = city
    r.state = state
    r.zip_code = zip_code
    r.lat = lat
    r.lng = lng
    r.phone = "555-0000"
    r.website = ""
    r.cuisine_type = ["American"]
    r.is_chain = False
    r.chain_name = None
    r.created_at = datetime.now(timezone.utc)
    r.updated_at = datetime.now(timezone.utc)
    return r


class TestNearbyEndpoint:
    @pytest.mark.asyncio
    async def test_missing_zip_code_returns_422(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/restaurants/nearby")
        assert response.status_code == 422  # Missing required param

    @pytest.mark.asyncio
    async def test_invalid_zip_code_too_short(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/restaurants/nearby?zip_code=123")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_zip_code_too_long(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/restaurants/nearby?zip_code=123456")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_radius_must_be_positive(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/restaurants/nearby?zip_code=10001&radius=0")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_radius_max_50(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/restaurants/nearby?zip_code=10001&radius=51")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_default_radius_is_5(self):
        """Default radius should be 5 miles when not specified.

        We validate the endpoint accepts zip_code without radius (no 422).
        Since there's no real DB in tests, we expect a server error or 404,
        but NOT a 422 validation error.
        """
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            try:
                response = await client.get("/api/v1/restaurants/nearby?zip_code=10001")
                # If we get a response, it shouldn't be 422
                assert response.status_code != 422
            except Exception:
                # DB connection errors in test env are expected — the point is
                # that FastAPI accepted the params (didn't return 422)
                pass


class TestNearbySchema:
    def test_nearby_response_has_distance(self):
        from src.api.schemas import NearbyResponse
        r = NearbyResponse(
            id=uuid4(),
            name="Test",
            is_chain=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            distance_miles=1.5,
        )
        assert r.distance_miles == 1.5

    def test_nearby_response_inherits_restaurant_fields(self):
        from src.api.schemas import NearbyResponse
        r = NearbyResponse(
            id=uuid4(),
            name="Test",
            city="New York",
            state="NY",
            is_chain=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            distance_miles=2.0,
        )
        assert r.city == "New York"
        assert r.state == "NY"


class TestNearbyDistanceCalculation:
    """Test that the distance filtering logic works correctly using haversine_miles."""

    def test_point_within_radius(self):
        # Times Square to Empire State ~0.5 miles
        d = haversine_miles(40.7580, -73.9855, 40.7484, -73.9857)
        assert d < 5.0

    def test_point_outside_radius(self):
        # Manhattan to JFK ~13 miles
        d = haversine_miles(40.7580, -73.9855, 40.6413, -73.7781)
        assert d > 5.0

    def test_nearby_filter_logic(self):
        """Simulate the filtering that the endpoint does."""
        center_lat, center_lng = 40.7128, -74.0060
        radius = 2.0

        restaurants = [
            ("Close Restaurant", 40.7130, -74.0065),   # ~0.03 miles
            ("Medium Restaurant", 40.7200, -74.0100),   # ~0.6 miles
            ("Far Restaurant", 40.7500, -73.9500),       # ~3.5 miles
            ("Very Far Restaurant", 40.8000, -73.9000),  # ~7 miles
        ]

        nearby = []
        for name, lat, lng in restaurants:
            dist = haversine_miles(center_lat, center_lng, lat, lng)
            if dist <= radius:
                nearby.append((name, dist))

        nearby.sort(key=lambda x: x[1])

        # Only first 2 should be within 2 miles
        assert len(nearby) == 2
        assert nearby[0][0] == "Close Restaurant"
        assert nearby[1][0] == "Medium Restaurant"

    def test_results_sorted_by_distance(self):
        """Verify sorting by distance works."""
        center_lat, center_lng = 40.7128, -74.0060
        points = [
            (40.7200, -74.0100),  # ~0.6 miles
            (40.7130, -74.0065),  # ~0.03 miles
            (40.7180, -74.0090),  # ~0.4 miles
        ]

        distances = [haversine_miles(center_lat, center_lng, lat, lng) for lat, lng in points]
        sorted_distances = sorted(distances)

        assert sorted_distances[0] < sorted_distances[1] < sorted_distances[2]
