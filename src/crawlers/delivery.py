"""DoorDash / UberEats crawler using Playwright."""

import asyncio
from typing import AsyncIterator

from src.crawlers.base import BaseCrawler
from src.utils.logging import get_logger

logger = get_logger("crawler.delivery")


class DeliveryCrawler(BaseCrawler):
    SOURCE = "delivery"

    async def crawl(self, query: str, location: str) -> AsyncIterator[dict]:
        """Crawl delivery platforms for restaurant listings."""
        async for record in self._crawl_doordash(query, location):
            yield record
        async for record in self._crawl_ubereats(query, location):
            yield record

    async def _crawl_doordash(self, query: str, location: str) -> AsyncIterator[dict]:
        """Crawl DoorDash listings."""
        from playwright.async_api import async_playwright

        self.logger.info("crawling_doordash", query=query, location=location)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            try:
                search_url = f"https://www.doordash.com/search/store/{query.replace(' ', '%20')}/"
                await page.goto(search_url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(3)

                cards = await page.locator('[data-anchor-id="StoreCard"]').all()
                if not cards:
                    cards = await page.locator('a[href*="/store/"]').all()

                self.logger.info("doordash_results", count=len(cards))

                for card in cards[:20]:
                    try:
                        name_el = card.locator("span").first
                        name = await name_el.inner_text() if await name_el.count() > 0 else ""
                        href = await card.get_attribute("href") or ""

                        record = {
                            "name": name.strip(),
                            "source": "doordash",
                            "source_url": f"https://www.doordash.com{href}" if href.startswith("/") else href,
                            "has_delivery": True,
                            "delivery_platform": "doordash",
                        }

                        text = await card.inner_text()
                        for line in text.split("\n"):
                            line = line.strip()
                            if "min" in line.lower() and any(c.isdigit() for c in line):
                                record["delivery_time"] = line
                            if "$" in line and "fee" in line.lower():
                                record["delivery_fee"] = line

                        if name:
                            yield record

                    except Exception as e:
                        self.logger.warning("doordash_card_error", error=str(e))
                        continue

            finally:
                await browser.close()

    async def _crawl_ubereats(self, query: str, location: str) -> AsyncIterator[dict]:
        """Crawl UberEats listings."""
        from playwright.async_api import async_playwright

        self.logger.info("crawling_ubereats", query=query, location=location)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            try:
                search_url = f"https://www.ubereats.com/search?q={query.replace(' ', '%20')}"
                await page.goto(search_url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(3)

                cards = await page.locator('a[data-testid*="store-card"], a[href*="/store/"]').all()
                self.logger.info("ubereats_results", count=len(cards))

                for card in cards[:20]:
                    try:
                        name = await card.get_attribute("aria-label") or ""
                        href = await card.get_attribute("href") or ""

                        if not name:
                            name_el = card.locator("h3, span").first
                            name = await name_el.inner_text() if await name_el.count() > 0 else ""

                        record = {
                            "name": name.strip(),
                            "source": "ubereats",
                            "source_url": f"https://www.ubereats.com{href}" if href.startswith("/") else href,
                            "has_delivery": True,
                            "delivery_platform": "ubereats",
                        }

                        if name:
                            yield record

                    except Exception as e:
                        self.logger.warning("ubereats_card_error", error=str(e))
                        continue

            finally:
                await browser.close()
