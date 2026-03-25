"""Google Places API crawler (replaces Playwright-based Google Maps scraper)."""

import re
from typing import AsyncIterator

from src.config import settings
from src.crawlers.base import BaseCrawler
from src.utils.logging import get_logger

logger = get_logger("crawler.google_places")

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.rating,places.userRatingCount,places.nationalPhoneNumber,"
    "places.websiteUri,places.location,places.primaryType,"
    "places.types,nextPageToken"
)


class GoogleMapsCrawler(BaseCrawler):
    SOURCE = "google_maps"

    async def crawl(self, query: str, location: str) -> AsyncIterator[dict]:
        """Crawl Google Places API for restaurant listings."""
        if not settings.google_places_api_key:
            raise ValueError("GOOGLE_PLACES_API_KEY is not set. Add it to your environment variables.")

        search_text = f"{query} in {location}"
        self.logger.info("starting_crawl", query=search_text)

        headers = {
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": FIELD_MASK,
            "Content-Type": "application/json",
        }

        page_token = None
        for page in range(settings.google_places_max_pages):
            try:
                body = {
                    "textQuery": search_text,
                    "includedType": "restaurant",
                    "languageCode": "en",
                    "maxResultCount": 20,
                }
                if page_token:
                    body["pageToken"] = page_token

                data = await self._fetch_json(
                    PLACES_SEARCH_URL,
                    method="POST",
                    headers=headers,
                    json_body=body,
                )

                places = data.get("places", [])
                if not places:
                    self.logger.info("no_more_results", page=page)
                    break

                self.logger.info("page_results", page=page, count=len(places))

                for place in places:
                    record = self._parse_place(place)
                    if record:
                        yield record

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

            except Exception as e:
                self.logger.error("search_error", page=page, error=str(e))
                if page == 0:
                    raise  # Re-raise if first page fails — likely an API key or config issue
                break

    def _parse_place(self, place: dict) -> dict | None:
        """Convert Google Places API response to standard record format."""
        try:
            display_name = place.get("displayName", {})
            location = place.get("location", {})
            address = place.get("formattedAddress", "")

            # Parse city/state/zip from formatted address
            city, state, zip_code = self._parse_address(address)

            # Get cuisine from types
            types = place.get("types", [])
            primary_type = place.get("primaryType", "")
            cuisine = self._types_to_cuisine(types, primary_type)

            return {
                "name": display_name.get("text", ""),
                "address": address,
                "city": city,
                "state": state,
                "zip_code": zip_code,
                "lat": location.get("latitude"),
                "lng": location.get("longitude"),
                "phone": place.get("nationalPhoneNumber", ""),
                "website": place.get("websiteUri", ""),
                "rating": place.get("rating"),
                "review_count": place.get("userRatingCount", 0),
                "cuisine": cuisine,
                "source": self.SOURCE,
                "source_url": f"https://www.google.com/maps/place/?q=place_id:{place.get('id', '')}",
                "google_place_id": place.get("id"),
            }
        except Exception as e:
            self.logger.warning("parse_error", error=str(e))
            return None

    def _parse_address(self, address: str) -> tuple[str, str, str]:
        """Extract city, state, zip from formatted address."""
        # Pattern: "123 Main St, New York, NY 10001, USA"
        match = re.search(r",\s*([^,]+),\s*([A-Z]{2})\s+(\d{5})", address)
        if match:
            return match.group(1).strip(), match.group(2), match.group(3)

        # Simpler pattern: "City, ST"
        match = re.search(r",\s*([^,]+),\s*([A-Z]{2})", address)
        if match:
            return match.group(1).strip(), match.group(2), ""

        return "", "", ""

    def _types_to_cuisine(self, types: list[str], primary_type: str) -> str:
        """Convert Google place types to cuisine string."""
        cuisine_map = {
            "chinese_restaurant": "Chinese",
            "italian_restaurant": "Italian",
            "japanese_restaurant": "Japanese",
            "mexican_restaurant": "Mexican",
            "indian_restaurant": "Indian",
            "thai_restaurant": "Thai",
            "korean_restaurant": "Korean",
            "vietnamese_restaurant": "Vietnamese",
            "french_restaurant": "French",
            "greek_restaurant": "Greek",
            "mediterranean_restaurant": "Mediterranean",
            "pizza_restaurant": "Pizza",
            "seafood_restaurant": "Seafood",
            "steak_house": "Steakhouse",
            "sushi_restaurant": "Sushi",
            "barbecue_restaurant": "BBQ",
            "hamburger_restaurant": "Burgers",
            "sandwich_shop": "Sandwiches",
            "coffee_shop": "Coffee",
            "bakery": "Bakery",
            "ice_cream_shop": "Ice Cream",
            "fast_food_restaurant": "Fast Food",
            "american_restaurant": "American",
            "vegan_restaurant": "Vegan",
            "vegetarian_restaurant": "Vegetarian",
        }

        # Check primary type first
        if primary_type in cuisine_map:
            return cuisine_map[primary_type]

        # Check all types
        for t in types:
            if t in cuisine_map:
                return cuisine_map[t]

        return "Restaurant"
