"""Yelp crawler using httpx with structured parsing."""

import json
import re
from typing import AsyncIterator

from src.crawlers.base import BaseCrawler
from src.utils.logging import get_logger

logger = get_logger("crawler.yelp")


class YelpCrawler(BaseCrawler):
    SOURCE = "yelp"
    BASE_URL = "https://www.yelp.com"

    async def crawl(self, query: str, location: str) -> AsyncIterator[dict]:
        """Crawl Yelp search results for restaurants."""
        self.logger.info("starting_crawl", query=query, location=location)

        async with self._get_client() as client:
            offset = 0
            max_pages = 5

            for page_num in range(max_pages):
                try:
                    search_url = (
                        f"{self.BASE_URL}/search?"
                        f"find_desc={query}&find_loc={location}&start={offset}"
                    )
                    html = await self._fetch(search_url, client)
                    listings = self._parse_search_results(html)

                    if not listings:
                        self.logger.info("no_more_results", page=page_num)
                        break

                    for listing in listings:
                        try:
                            if listing.get("detail_url"):
                                detail_html = await self._fetch(
                                    f"{self.BASE_URL}{listing['detail_url']}", client
                                )
                                listing.update(self._parse_detail(detail_html))
                        except Exception as e:
                            self.logger.warning("detail_fetch_error", error=str(e))

                        listing["source"] = self.SOURCE
                        yield listing

                    offset += 10

                except Exception as e:
                    self.logger.error("search_page_error", page=page_num, error=str(e))
                    break

    def _parse_search_results(self, html: str) -> list[dict]:
        """Parse Yelp search results HTML."""
        results = []

        # Extract JSON-LD structured data
        json_pattern = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
        )
        for blob in json_pattern:
            try:
                data = json.loads(blob)
                if isinstance(data, list):
                    for item in data:
                        if item.get("@type") in ("Restaurant", "LocalBusiness"):
                            results.append(self._normalize_jsonld(item))
                elif isinstance(data, dict) and data.get("@type") in ("Restaurant", "LocalBusiness"):
                    results.append(self._normalize_jsonld(data))
            except json.JSONDecodeError:
                continue

        # Fallback: regex extraction for business cards
        if not results:
            name_pattern = re.findall(
                r'<a[^>]*href="(/biz/[^"]*)"[^>]*>([^<]+)</a>', html
            )
            for url, name in name_pattern[:10]:
                if "/biz/" in url:
                    results.append({"name": name.strip(), "detail_url": url})

        return results

    def _normalize_jsonld(self, data: dict) -> dict:
        """Convert JSON-LD to our standard format."""
        address = data.get("address", {})
        serves_cuisine = data.get("servesCuisine", [])
        return {
            "name": data.get("name", ""),
            "address": address.get("streetAddress", ""),
            "city": address.get("addressLocality", ""),
            "state": address.get("addressRegion", ""),
            "zip_code": address.get("postalCode", ""),
            "rating": data.get("aggregateRating", {}).get("ratingValue"),
            "review_count": data.get("aggregateRating", {}).get("reviewCount"),
            "phone": data.get("telephone", ""),
            "cuisine": ", ".join(serves_cuisine) if isinstance(serves_cuisine, list) else serves_cuisine,
            "source_url": data.get("url", ""),
        }

    def _parse_detail(self, html: str) -> dict:
        """Extract additional details from a Yelp business page."""
        details = {}

        if any(kw in html.lower() for kw in ["doordash", "ubereats", "grubhub", "delivery available"]):
            details["has_delivery"] = True

        pos_keywords = ["toast", "square", "clover", "lightspeed", "aloha", "micros"]
        for kw in pos_keywords:
            if kw in html.lower():
                details["pos_indicator"] = kw
                break

        return details
