"""Tests for Yelp Fusion crawler."""

import pytest
from unittest.mock import AsyncMock, patch

from src.crawlers.yelp import YelpCrawler


class TestYelpCrawler:
    def setup_method(self):
        self.crawler = YelpCrawler()

    def test_parse_business(self, sample_yelp_response):
        biz = sample_yelp_response["businesses"][0]
        record = self.crawler._parse_business(biz)

        assert record["name"] == "Test Restaurant"
        assert record["address"] == "123 Main St"
        assert record["city"] == "New York"
        assert record["state"] == "NY"
        assert record["zip_code"] == "10001"
        assert record["lat"] == 40.7128
        assert record["lng"] == -74.0060
        assert record["rating"] == 4.5
        assert record["review_count"] == 200
        assert "Italian" in record["cuisine"]
        assert record["price"] == "$$"
        assert record["source"] == "yelp"

    def test_parse_business_empty_location(self):
        biz = {"name": "Test", "location": {}, "coordinates": {}, "categories": []}
        record = self.crawler._parse_business(biz)
        assert record["name"] == "Test"
        assert record["address"] == ""
        assert record["lat"] is None

    def test_parse_business_no_categories(self):
        biz = {"name": "Test", "location": {}, "coordinates": {}, "categories": None}
        record = self.crawler._parse_business(biz)
        assert record["cuisine"] == ""

    @pytest.mark.asyncio
    async def test_crawl_raises_without_api_key(self):
        with patch("src.crawlers.yelp.settings") as mock_settings:
            mock_settings.yelp_fusion_api_key = ""
            crawler = YelpCrawler()
            with pytest.raises(ValueError, match="YELP_FUSION_API_KEY"):
                async for _ in crawler.crawl("restaurants", "New York"):
                    pass

    @pytest.mark.asyncio
    async def test_crawl_yields_results(self, sample_yelp_response):
        with patch("src.crawlers.yelp.settings") as mock_settings:
            mock_settings.yelp_fusion_api_key = "test-key"
            mock_settings.crawl_concurrency = 1
            mock_settings.rate_limit_per_second = 100
            crawler = YelpCrawler()
            crawler._fetch_json = AsyncMock(side_effect=[
                sample_yelp_response,  # search
                {"transactions": ["delivery"], "hours": [], "photos": []},  # detail
            ])

            results = []
            async for record in crawler.crawl("restaurants", "New York"):
                results.append(record)

            assert len(results) == 1
            assert results[0]["name"] == "Test Restaurant"
