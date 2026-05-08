"""Tests for Address Normalization Service (NIF-263)."""

import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.services.address_normalization import (
    normalize_address,
    geocode_restaurant,
    batch_normalize,
)


MOCK_GEOCODING_RESPONSE = {
    "status": "OK",
    "results": [
        {
            "formatted_address": "123 Main St, New York, NY 10001, USA",
            "place_id": "ChIJ_abc123",
            "geometry": {"location": {"lat": 40.7128, "lng": -74.0060}},
            "address_components": [
                {"long_name": "123", "types": ["street_number"]},
                {"long_name": "Main Street", "types": ["route"]},
                {"long_name": "New York", "types": ["locality"]},
                {"long_name": "NY", "short_name": "NY", "types": ["administrative_area_level_1"]},
                {"long_name": "10001", "types": ["postal_code"]},
                {"long_name": "United States", "short_name": "US", "types": ["country"]},
            ],
        }
    ],
}


class TestNormalizeAddress:
    """NIF-263: Address normalization via Geocoding API."""

    @pytest.mark.asyncio
    async def test_normalize_no_api_key(self):
        """Returns error when no API key configured."""
        with patch("src.services.address_normalization.settings") as mock_settings:
            mock_settings.effective_geocoding_key = ""
            result = await normalize_address("123 Main St")
            assert "error" in result
            assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_normalize_success(self):
        """Successful normalization returns structured components."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_GEOCODING_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.services.address_normalization.settings") as mock_settings, \
             patch("src.services.address_normalization.httpx.AsyncClient", return_value=mock_client):
            mock_settings.effective_geocoding_key = "test-key"
            result = await normalize_address("123 Main St", "New York", "NY", "10001")

        assert result["formatted_address"] == "123 Main St, New York, NY 10001, USA"
        assert result["lat"] == 40.7128
        assert result["lng"] == -74.0060
        assert result["city"] == "New York"
        assert result["state"] == "NY"
        assert result["zip_code"] == "10001"
        assert result["country"] == "US"
        assert result["address"] == "123 Main Street"
        assert result["place_id"] == "ChIJ_abc123"

    @pytest.mark.asyncio
    async def test_normalize_geocoding_failure(self):
        """Returns error when geocoding fails."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ZERO_RESULTS", "results": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.services.address_normalization.settings") as mock_settings, \
             patch("src.services.address_normalization.httpx.AsyncClient", return_value=mock_client):
            mock_settings.effective_geocoding_key = "test-key"
            result = await normalize_address("nonexistent address xyz")

        assert "error" in result
        assert "ZERO_RESULTS" in result.get("status", "")


class TestNormalizeAddressComponents:
    """NIF-263: Test address component extraction."""

    @pytest.mark.asyncio
    async def test_street_number_and_name(self):
        """Builds address from street_number + street_name."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_GEOCODING_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.services.address_normalization.settings") as mock_settings, \
             patch("src.services.address_normalization.httpx.AsyncClient", return_value=mock_client):
            mock_settings.effective_geocoding_key = "test-key"
            result = await normalize_address("123 Main St")

        assert result["street_number"] == "123"
        assert result["street_name"] == "Main Street"
        assert result["address"] == "123 Main Street"


class TestAddressRouter:
    """NIF-263: Address router registration and endpoints."""

    def test_router_importable(self):
        from src.api.routers.address import router
        assert router.prefix == "/address"

    def test_router_has_normalize_endpoint(self):
        from src.api.routers.address import router
        paths = [r.path for r in router.routes]
        assert "/address/normalize" in paths

    def test_router_has_batch_normalize_endpoint(self):
        from src.api.routers.address import router
        paths = [r.path for r in router.routes]
        assert "/address/batch-normalize" in paths

    def test_router_has_geocode_endpoint(self):
        from src.api.routers.address import router
        paths = [r.path for r in router.routes]
        assert "/address/geocode/{restaurant_id}" in paths

    def test_router_registered_in_app(self):
        from src.main import app
        paths = [r.path for r in app.routes]
        address_paths = [p for p in paths if "/address" in p]
        assert len(address_paths) > 0

    def test_router_tags(self):
        from src.api.routers.address import router
        assert "address" in router.tags


class TestConfigGeocodingKey:
    """NIF-263: Config settings for geocoding."""

    def test_effective_geocoding_key_primary(self):
        from src.config import Settings
        s = Settings(google_geocoding_api_key="geo-key", google_places_api_key="places-key")
        assert s.effective_geocoding_key == "geo-key"

    def test_effective_geocoding_key_fallback(self):
        from src.config import Settings
        s = Settings(google_geocoding_api_key="", google_places_api_key="places-key")
        assert s.effective_geocoding_key == "places-key"

    def test_effective_geocoding_key_empty(self):
        from src.config import Settings
        s = Settings(google_geocoding_api_key="", google_places_api_key="")
        assert s.effective_geocoding_key == ""
