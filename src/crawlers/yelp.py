"""Yelp Fusion API crawler (replaces HTML scraping)."""

from typing import AsyncIterator

from src.config import settings
from src.crawlers.base import BaseCrawler
from src.utils.logging import get_logger

logger = get_logger("crawler.yelp")

YELP_SEARCH_URL = "https://api.yelp.com/v3/businesses/search"
YELP_DETAIL_URL = "https://api.yelp.com/v3/businesses"


class YelpCrawler(BaseCrawler):
    SOURCE = "yelp"

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {settings.yelp_fusion_api_key}"}

    async def crawl(self, query: str, location: str) -> AsyncIterator[dict]:
        """Crawl Yelp Fusion API for restaurant listings."""
        if not settings.yelp_fusion_api_key:
            raise ValueError("YELP_FUSION_API_KEY is not set. Add it to your environment variables.")

        self.logger.info("starting_crawl", query=query, location=location)
        headers = self._auth_headers()
        max_pages = 5
        limit = 50  # Max per request

        for page in range(max_pages):
            offset = page * limit
            try:
                params = {
                    "term": query,
                    "location": location,
                    "categories": "restaurants",
                    "limit": limit,
                    "offset": offset,
                    "sort_by": "review_count",
                }

                data = await self._fetch_json(
                    YELP_SEARCH_URL,
                    headers=headers,
                    params=params,
                )

                businesses = data.get("businesses", [])
                if not businesses:
                    self.logger.info("no_more_results", page=page)
                    break

                self.logger.info("page_results", page=page, count=len(businesses))

                for biz in businesses:
                    record = self._parse_business(biz)

                    # Fetch details for delivery/transaction info
                    try:
                        detail = await self._fetch_detail(biz["id"], headers)
                        if detail:
                            record.update(detail)
                    except Exception as e:
                        self.logger.warning("detail_error", biz_id=biz["id"], error=str(e))

                    yield record

                # Check if we've fetched all results
                total = data.get("total", 0)
                if offset + limit >= total:
                    break

            except Exception as e:
                self.logger.error("search_error", page=page, error=str(e))
                break

    async def _fetch_detail(self, biz_id: str, headers: dict) -> dict | None:
        """Fetch additional business details from Yelp."""
        try:
            data = await self._fetch_json(
                f"{YELP_DETAIL_URL}/{biz_id}",
                headers=headers,
            )
            result = {}

            # Transaction types (delivery, pickup, restaurant_reservation)
            transactions = data.get("transactions", [])
            if "delivery" in transactions:
                result["has_delivery"] = True
                result["delivery_platform"] = "yelp_delivery"
            if transactions:
                result["transactions"] = transactions

            # Hours
            hours = data.get("hours", [])
            if hours:
                result["is_open_now"] = hours[0].get("is_open_now", False)

            # Photos
            photos = data.get("photos", [])
            if photos:
                result["photo_count"] = len(photos)

            return result

        except Exception as e:
            self.logger.warning("detail_fetch_error", biz_id=biz_id, error=str(e))
            return None

    def _parse_business(self, biz: dict) -> dict:
        """Convert Yelp Fusion business to standard record format."""
        location = biz.get("location", {})
        coordinates = biz.get("coordinates", {})
        categories = biz.get("categories", [])

        return {
            "name": biz.get("name", ""),
            "address": location.get("address1", ""),
            "city": location.get("city", ""),
            "state": location.get("state", ""),
            "zip_code": location.get("zip_code", ""),
            "lat": coordinates.get("latitude"),
            "lng": coordinates.get("longitude"),
            "phone": biz.get("display_phone", "") or biz.get("phone", ""),
            "rating": biz.get("rating"),
            "review_count": biz.get("review_count", 0),
            "cuisine": ", ".join(c.get("title", "") for c in categories) if categories else "",
            "source": self.SOURCE,
            "source_url": biz.get("url", ""),
            "yelp_id": biz.get("id", ""),
            "price": biz.get("price", ""),
            "is_closed": biz.get("is_closed", False),
        }
