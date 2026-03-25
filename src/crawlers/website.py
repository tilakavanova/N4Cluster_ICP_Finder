"""Generic website crawler for restaurant detail pages (httpx only, no Playwright)."""

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
            async with self._get_client() as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content = resp.text
        except Exception as e:
            self.logger.warning("fetch_failed", url=url, error=str(e))
            return {
                "source": self.SOURCE,
                "source_url": url,
                "raw_text": "",
                "raw_html": "",
                "error": str(e),
            }

        return {
            "source": self.SOURCE,
            "source_url": url,
            "raw_text": self._clean_text(content),
            "raw_html": content[:50000],
        }

    def _clean_text(self, html: str) -> str:
        """Strip HTML tags and normalize whitespace."""
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()[:10000]
