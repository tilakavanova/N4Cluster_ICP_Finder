"""Tests for base crawler functionality."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.crawlers.base import BaseCrawler


class ConcreteCrawler(BaseCrawler):
    """Concrete implementation for testing abstract base."""
    SOURCE = "test"

    async def crawl(self, query, location):
        yield {"name": "test"}


class TestBaseCrawler:
    def setup_method(self):
        self.crawler = ConcreteCrawler()

    def test_source_set(self):
        assert self.crawler.SOURCE == "test"

    @pytest.mark.asyncio
    async def test_run_collects_results(self):
        results = await self.crawler.run("q", "loc")
        assert len(results) == 1
        assert results[0]["name"] == "test"

    def test_get_proxy_none_by_default(self):
        proxy = self.crawler._get_proxy()
        # Default: no proxies configured
        assert proxy is None

    @pytest.mark.asyncio
    async def test_throttle_executes(self):
        # Should not raise
        await self.crawler._throttle()
