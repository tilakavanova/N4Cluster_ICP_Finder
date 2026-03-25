"""Base crawler with retry, rate limiting, and proxy support."""

import asyncio
from abc import ABC, abstractmethod
from typing import AsyncIterator

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import settings
from src.utils.logging import get_logger
from src.utils.proxy import proxy_pool


class BaseCrawler(ABC):
    """Abstract base for all crawlers."""

    SOURCE: str = "base"

    def __init__(self):
        self.logger = get_logger(f"crawler.{self.SOURCE}")
        self._semaphore = asyncio.Semaphore(settings.crawl_concurrency)
        self._rate_limit = 1.0 / settings.rate_limit_per_second

    async def _throttle(self):
        """Enforce rate limiting between requests."""
        await asyncio.sleep(self._rate_limit)

    def _get_proxy(self) -> str | None:
        return proxy_pool.next_proxy()

    def _get_client(self, **kwargs) -> httpx.AsyncClient:
        proxy = self._get_proxy()
        return httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            proxy=proxy,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
            **kwargs,
        )

    @retry(
        stop=stop_after_attempt(settings.crawl_retry_attempts),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)),
        reraise=True,
    )
    async def _fetch(self, url: str, client: httpx.AsyncClient | None = None) -> str:
        """Fetch a URL with retry and rate limiting."""
        async with self._semaphore:
            await self._throttle()
            if client:
                resp = await client.get(url)
            else:
                async with self._get_client() as c:
                    resp = await c.get(url)
            resp.raise_for_status()
            self.logger.info("fetched", url=url, status=resp.status_code)
            return resp.text

    @abstractmethod
    async def crawl(self, query: str, location: str) -> AsyncIterator[dict]:
        """Crawl a source and yield raw records."""
        ...

    async def run(self, query: str, location: str) -> list[dict]:
        """Execute crawl and collect all results."""
        results = []
        async for record in self.crawl(query, location):
            results.append(record)
        self.logger.info("crawl_complete", source=self.SOURCE, count=len(results))
        return results
