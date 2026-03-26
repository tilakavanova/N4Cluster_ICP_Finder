"""Tests for Google Maps crawler."""

import pytest
from unittest.mock import AsyncMock, patch

from src.crawlers.google_maps import GoogleMapsCrawler


class TestGoogleMapsCrawler:
    def setup_method(self):
        self.crawler = GoogleMapsCrawler()

    def test_parse_address_full(self):
        city, state, zip_code = self.crawler._parse_address("123 Main St, New York, NY 10001, USA")
        assert city == "New York"
        assert state == "NY"
        assert zip_code == "10001"

    def test_parse_address_no_zip(self):
        city, state, zip_code = self.crawler._parse_address("123 Main St, Brooklyn, NY")
        assert city == "Brooklyn"
        assert state == "NY"
        assert zip_code == ""

    def test_parse_address_unparseable(self):
        city, state, zip_code = self.crawler._parse_address("Some random text")
        assert city == ""
        assert state == ""
        assert zip_code == ""

    def test_parse_place_valid(self, sample_google_places_response):
        place = sample_google_places_response["places"][0]
        record = self.crawler._parse_place(place)
        assert record is not None
        assert record["name"] == "Test Restaurant"
        assert record["city"] == "New York"
        assert record["state"] == "NY"
        assert record["zip_code"] == "10001"
        assert record["lat"] == 40.7128
        assert record["lng"] == -74.0060
        assert record["phone"] == "(555) 123-4567"
        assert record["source"] == "google_maps"
        assert record["cuisine"] == "Italian"

    def test_parse_place_missing_fields(self):
        place = {"displayName": {"text": "Minimal"}, "formattedAddress": "", "location": {}, "types": []}
        record = self.crawler._parse_place(place)
        assert record is not None
        assert record["name"] == "Minimal"

    def test_types_to_cuisine_primary(self):
        assert self.crawler._types_to_cuisine(["restaurant"], "italian_restaurant") == "Italian"

    def test_types_to_cuisine_from_types(self):
        assert self.crawler._types_to_cuisine(["sushi_restaurant", "restaurant"], "") == "Sushi"

    def test_types_to_cuisine_default(self):
        assert self.crawler._types_to_cuisine(["restaurant"], "") == "Restaurant"

    def test_types_to_cuisine_all_mapped(self):
        mapped_types = [
            "chinese_restaurant", "italian_restaurant", "japanese_restaurant",
            "mexican_restaurant", "indian_restaurant", "thai_restaurant",
            "pizza_restaurant", "seafood_restaurant", "fast_food_restaurant",
        ]
        for t in mapped_types:
            result = self.crawler._types_to_cuisine([t], "")
            assert result != "Restaurant", f"Type {t} should be mapped"

    @pytest.mark.asyncio
    async def test_crawl_raises_without_api_key(self):
        with patch("src.crawlers.google_maps.settings") as mock_settings:
            mock_settings.google_places_api_key = ""
            crawler = GoogleMapsCrawler()
            with pytest.raises(ValueError, match="GOOGLE_PLACES_API_KEY"):
                async for _ in crawler.crawl("restaurants", "New York"):
                    pass

    @pytest.mark.asyncio
    async def test_crawl_yields_results(self, sample_google_places_response):
        with patch("src.crawlers.google_maps.settings") as mock_settings:
            mock_settings.google_places_api_key = "test-key"
            mock_settings.google_places_max_pages = 1
            mock_settings.crawl_concurrency = 1
            mock_settings.rate_limit_per_second = 100
            crawler = GoogleMapsCrawler()
            crawler._fetch_json = AsyncMock(return_value=sample_google_places_response)

            results = []
            async for record in crawler.crawl("restaurants", "New York"):
                results.append(record)

            assert len(results) == 1
            assert results[0]["name"] == "Test Restaurant"
