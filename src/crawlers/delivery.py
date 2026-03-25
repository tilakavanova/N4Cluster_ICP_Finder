"""Delivery platform detection via Yelp transactions + optional SerpAPI verification."""

from typing import AsyncIterator

from src.config import settings
from src.crawlers.base import BaseCrawler
from src.utils.logging import get_logger

logger = get_logger("crawler.delivery")

YELP_SEARCH_URL = "https://api.yelp.com/v3/businesses/search"
SERPAPI_URL = "https://serpapi.com/search.json"


class DeliveryCrawler(BaseCrawler):
    SOURCE = "delivery"

    async def crawl(self, query: str, location: str) -> AsyncIterator[dict]:
        """Detect delivery-enabled restaurants via Yelp + SerpAPI."""
        # Primary: Yelp Fusion with delivery filter
        if settings.yelp_fusion_api_key:
            async for record in self._crawl_yelp_delivery(query, location):
                yield record
        # Fallback/supplement: SerpAPI
        elif settings.serpapi_api_key:
            async for record in self._crawl_serpapi(query, location):
                yield record
        else:
            raise ValueError("Need YELP_FUSION_API_KEY or SERPAPI_API_KEY for delivery crawling. Add at least one to your environment variables.")

    async def _crawl_yelp_delivery(self, query: str, location: str) -> AsyncIterator[dict]:
        """Find delivery-enabled restaurants via Yelp Fusion API."""
        self.logger.info("crawling_yelp_delivery", query=query, location=location)
        headers = {"Authorization": f"Bearer {settings.yelp_fusion_api_key}"}

        for page in range(3):
            try:
                params = {
                    "term": query,
                    "location": location,
                    "categories": "restaurants",
                    "limit": 50,
                    "offset": page * 50,
                    "attributes": "delivery",
                }

                data = await self._fetch_json(YELP_SEARCH_URL, headers=headers, params=params)
                businesses = data.get("businesses", [])

                if not businesses:
                    break

                self.logger.info("delivery_results", page=page, count=len(businesses))

                for biz in businesses:
                    location_data = biz.get("location", {})
                    coordinates = biz.get("coordinates", {})
                    categories = biz.get("categories", [])
                    transactions = biz.get("transactions", [])

                    # Determine delivery platforms
                    platforms = []
                    if "delivery" in transactions:
                        platforms.append("yelp_delivery")

                    record = {
                        "name": biz.get("name", ""),
                        "address": location_data.get("address1", ""),
                        "city": location_data.get("city", ""),
                        "state": location_data.get("state", ""),
                        "zip_code": location_data.get("zip_code", ""),
                        "lat": coordinates.get("latitude"),
                        "lng": coordinates.get("longitude"),
                        "phone": biz.get("display_phone", ""),
                        "rating": biz.get("rating"),
                        "review_count": biz.get("review_count", 0),
                        "cuisine": ", ".join(c.get("title", "") for c in categories),
                        "source": "delivery",
                        "source_url": biz.get("url", ""),
                        "has_delivery": True,
                        "delivery_platform": "yelp_delivery",
                        "delivery_platforms": platforms,
                        "transactions": transactions,
                    }

                    # Optionally verify with SerpAPI for DoorDash/UberEats presence
                    if settings.serpapi_api_key:
                        extra_platforms = await self._check_delivery_platforms(
                            biz.get("name", ""), location_data.get("city", "")
                        )
                        if extra_platforms:
                            record["delivery_platforms"].extend(extra_platforms)

                    yield record

                total = data.get("total", 0)
                if (page + 1) * 50 >= total:
                    break

            except Exception as e:
                self.logger.error("yelp_delivery_error", page=page, error=str(e))
                break

    async def _crawl_serpapi(self, query: str, location: str) -> AsyncIterator[dict]:
        """Find delivery restaurants via SerpAPI Google search."""
        self.logger.info("crawling_serpapi", query=query, location=location)

        try:
            params = {
                "engine": "google",
                "q": f"{query} delivery {location}",
                "api_key": settings.serpapi_api_key,
                "num": 20,
            }

            data = await self._fetch_json(SERPAPI_URL, params=params)
            results = data.get("organic_results", [])

            for result in results:
                link = result.get("link", "").lower()
                title = result.get("title", "")
                snippet = result.get("snippet", "")

                platforms = []
                if "doordash.com" in link:
                    platforms.append("doordash")
                if "ubereats.com" in link:
                    platforms.append("ubereats")
                if "grubhub.com" in link:
                    platforms.append("grubhub")

                if platforms:
                    yield {
                        "name": title.split(" - ")[0].split(" | ")[0].strip(),
                        "source": "delivery",
                        "source_url": result.get("link", ""),
                        "has_delivery": True,
                        "delivery_platform": platforms[0],
                        "delivery_platforms": platforms,
                    }

        except Exception as e:
            self.logger.error("serpapi_error", error=str(e))

    async def _check_delivery_platforms(self, name: str, city: str) -> list[str]:
        """Check if a restaurant is on DoorDash/UberEats via SerpAPI."""
        platforms = []
        try:
            params = {
                "engine": "google",
                "q": f'"{name}" {city} site:doordash.com OR site:ubereats.com',
                "api_key": settings.serpapi_api_key,
                "num": 5,
            }

            data = await self._fetch_json(SERPAPI_URL, params=params)
            for result in data.get("organic_results", []):
                link = result.get("link", "").lower()
                if "doordash.com" in link and "doordash" not in platforms:
                    platforms.append("doordash")
                if "ubereats.com" in link and "ubereats" not in platforms:
                    platforms.append("ubereats")

        except Exception as e:
            self.logger.warning("platform_check_error", name=name, error=str(e))

        return platforms
