"""Tests for delivery platform crawler."""

import pytest
from unittest.mock import AsyncMock, patch

from src.crawlers.delivery import DeliveryCrawler


class TestDeliveryCrawler:
    @pytest.mark.asyncio
    async def test_raises_without_any_key(self):
        with patch("src.crawlers.delivery.settings") as mock_settings:
            mock_settings.yelp_fusion_api_key = ""
            mock_settings.serpapi_api_key = ""
            mock_settings.crawl_concurrency = 1
            mock_settings.rate_limit_per_second = 100
            crawler = DeliveryCrawler()
            with pytest.raises(ValueError, match="YELP_FUSION_API_KEY or SERPAPI_API_KEY"):
                async for _ in crawler.crawl("restaurants", "New York"):
                    pass

    @pytest.mark.asyncio
    async def test_uses_yelp_when_key_present(self):
        with patch("src.crawlers.delivery.settings") as mock_settings:
            mock_settings.yelp_fusion_api_key = "test-key"
            mock_settings.serpapi_api_key = ""
            mock_settings.crawl_concurrency = 1
            mock_settings.rate_limit_per_second = 100
            crawler = DeliveryCrawler()

            yelp_response = {
                "businesses": [{
                    "name": "Test",
                    "location": {"address1": "123 St", "city": "NY", "state": "NY", "zip_code": "10001"},
                    "coordinates": {"latitude": 40.71, "longitude": -74.00},
                    "display_phone": "",
                    "rating": 4.0,
                    "review_count": 100,
                    "categories": [{"title": "Pizza"}],
                    "url": "",
                    "transactions": ["delivery"],
                }],
                "total": 1,
            }
            crawler._fetch_json = AsyncMock(return_value=yelp_response)

            results = []
            async for record in crawler.crawl("restaurants", "New York"):
                results.append(record)

            assert len(results) == 1
            assert results[0]["has_delivery"] is True
            assert "yelp_delivery" in results[0]["delivery_platforms"]
