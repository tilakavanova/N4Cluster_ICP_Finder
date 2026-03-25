"""Generic website crawler for restaurant detail pages."""

import re
from typing import AsyncIterator

from src.crawlers.base import BaseCrawler
from src.utils.logging import get_logger

logger = get_logger("crawler.website")


class WebsiteCrawler(BaseCrawler):
    SOURCE = "website"

    async def crawl(self, query: str, location: str) -> AsyncIterator[dict]:
        """Not used for website crawler — use crawl_url instead."""
        raise NotImplementedError("Use crawl_url() for website crawling")

    async def crawl_url(self, url: str) -> dict:
        """Crawl a specific restaurant website and extract raw content."""
        self.logger.info("crawling_website", url=url)

        try:
            content = await self._fetch_simple(url)
        except Exception:
            self.logger.info("falling_back_to_playwright", url=url)
            content = await self._fetch_playwright(url)

        return {
            "source": self.SOURCE,
            "source_url": url,
            "raw_text": self._clean_text(content),
            "raw_html": content[:50000],
        }

    async def _fetch_simple(self, url: str) -> str:
        """Fetch with httpx."""
        async with self._get_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def _fetch_playwright(self, url: str) -> str:
        """Fetch with Playwright for JS-rendered pages."""
        from playwright.async_api import async_playwright
        import asyncio

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=20000)
                await asyncio.sleep(2)
                return await page.content()
            finally:
                await browser.close()

    def _clean_text(self, html: str) -> str:
        """Strip HTML tags and normalize whitespace."""
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()[:10000]
