"""Google Maps crawler using Playwright."""

import asyncio
from typing import AsyncIterator

from src.crawlers.base import BaseCrawler
from src.utils.logging import get_logger

logger = get_logger("crawler.google_maps")


class GoogleMapsCrawler(BaseCrawler):
    SOURCE = "google_maps"

    async def crawl(self, query: str, location: str) -> AsyncIterator[dict]:
        """Crawl Google Maps for restaurant listings."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.logger.warning("playwright_not_available", msg="Skipping Google Maps crawl — install Playwright for browser-based crawling")
            return

        search_query = f"{query} in {location}"
        self.logger.info("starting_crawl", query=search_query)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            page = await context.new_page()

            try:
                url = f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(2)

                # Scroll to load more results
                feed = page.locator('div[role="feed"]')
                for _ in range(5):
                    await feed.evaluate("el => el.scrollTop = el.scrollHeight")
                    await asyncio.sleep(1.5)

                # Extract listings
                listings = await page.locator('div[role="feed"] > div > div > a').all()
                self.logger.info("found_listings", count=len(listings))

                for listing in listings:
                    try:
                        aria_label = await listing.get_attribute("aria-label") or ""
                        href = await listing.get_attribute("href") or ""

                        if not aria_label:
                            continue

                        await listing.click()
                        await asyncio.sleep(1.5)

                        record = await self._extract_detail(page, aria_label, href)
                        if record:
                            yield record

                    except Exception as e:
                        self.logger.warning("listing_error", error=str(e))
                        continue

            finally:
                await browser.close()

    async def _extract_detail(self, page, name: str, url: str) -> dict | None:
        """Extract details from a Google Maps listing detail panel."""
        try:
            detail = {
                "name": name,
                "source_url": url,
                "source": self.SOURCE,
            }

            # Address
            addr_el = page.locator('button[data-item-id="address"]')
            if await addr_el.count() > 0:
                detail["address"] = (await addr_el.first.get_attribute("aria-label") or "").replace("Address: ", "")

            # Rating
            rating_el = page.locator('div[role="img"][aria-label*="stars"]')
            if await rating_el.count() > 0:
                label = await rating_el.first.get_attribute("aria-label") or ""
                parts = label.split()
                if parts:
                    try:
                        detail["rating"] = float(parts[0])
                    except ValueError:
                        pass

            # Review count
            review_el = page.locator('button[jsaction*="reviewChart"]')
            if await review_el.count() > 0:
                text = await review_el.first.inner_text()
                nums = "".join(c for c in text if c.isdigit())
                if nums:
                    detail["review_count"] = int(nums)

            # Phone
            phone_el = page.locator('button[data-item-id*="phone"]')
            if await phone_el.count() > 0:
                detail["phone"] = (await phone_el.first.get_attribute("aria-label") or "").replace("Phone: ", "")

            # Website
            web_el = page.locator('a[data-item-id="authority"]')
            if await web_el.count() > 0:
                detail["website"] = await web_el.first.get_attribute("href") or ""

            # Category / cuisine
            cat_el = page.locator('button[jsaction*="category"]')
            if await cat_el.count() > 0:
                detail["cuisine"] = await cat_el.first.inner_text()

            # Coordinates from URL
            if "/@" in url:
                try:
                    coords = url.split("/@")[1].split(",")[:2]
                    detail["lat"] = float(coords[0])
                    detail["lng"] = float(coords[1])
                except (IndexError, ValueError):
                    pass

            return detail

        except Exception as e:
            self.logger.warning("detail_extraction_error", error=str(e))
            return None
