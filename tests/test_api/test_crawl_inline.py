"""Tests for inline crawl execution (no Celery)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from src.db.models import CrawlJob


class TestInlineCrawlConfig:
    """Test USE_CELERY config flag."""

    def test_default_use_celery_false(self):
        from src.config import Settings
        s = Settings()
        assert s.use_celery is False

    def test_use_celery_true(self):
        from src.config import Settings
        s = Settings(use_celery=True)
        assert s.use_celery is True


class TestGetCrawler:
    """Test crawler factory function."""

    def test_google_maps_crawler(self):
        from src.tasks.crawl_tasks import _get_crawler
        crawler = _get_crawler("google_maps")
        assert crawler is not None
        assert crawler.__class__.__name__ == "GoogleMapsCrawler"

    def test_yelp_crawler(self):
        from src.tasks.crawl_tasks import _get_crawler
        crawler = _get_crawler("yelp")
        assert crawler is not None
        assert crawler.__class__.__name__ == "YelpCrawler"

    def test_delivery_crawler(self):
        from src.tasks.crawl_tasks import _get_crawler
        crawler = _get_crawler("delivery")
        assert crawler is not None

    def test_website_crawler(self):
        from src.tasks.crawl_tasks import _get_crawler
        crawler = _get_crawler("website")
        assert crawler is not None

    def test_unknown_source_returns_none(self):
        from src.tasks.crawl_tasks import _get_crawler
        assert _get_crawler("nonexistent") is None
        assert _get_crawler("") is None


class TestInlineCrawlFunction:
    """Test _run_crawl_inline logic."""

    @pytest.mark.asyncio
    async def test_unknown_source_marks_failed(self):
        """Unknown source should mark the job as failed."""
        from src.api.routers.jobs import _run_crawl_inline

        job_id = str(uuid4())
        mock_job = MagicMock(spec=CrawlJob)
        mock_job.id = job_id

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_job)
        mock_session.commit = AsyncMock()

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=False)

        with patch("src.api.routers.jobs.async_session", return_value=mock_context):
            await _run_crawl_inline("nonexistent_source", "restaurants", "NYC", job_id)

        # Verify the job was marked as failed
        assert mock_job.status == "failed"
        assert "Unknown source" in mock_job.error_message

    @pytest.mark.asyncio
    async def test_crawler_exception_marks_failed(self):
        """Crawler that throws should mark job as failed with error message."""
        from src.api.routers.jobs import _run_crawl_inline

        job_id = str(uuid4())
        mock_job = MagicMock(spec=CrawlJob)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_job)
        mock_session.commit = AsyncMock()

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=False)

        mock_crawler = MagicMock()
        mock_crawler.run = AsyncMock(side_effect=Exception("API key missing"))

        with patch("src.api.routers.jobs.async_session", return_value=mock_context), \
             patch("src.tasks.crawl_tasks._get_crawler", return_value=mock_crawler):
            await _run_crawl_inline("google_maps", "restaurants", "NYC", job_id)

        # Job should be marked failed
        assert mock_job.status == "failed"
        assert "API key missing" in mock_job.error_message

    @pytest.mark.asyncio
    async def test_successful_crawl_marks_completed(self):
        """Successful crawl should mark job as completed with item count."""
        from src.api.routers.jobs import _run_crawl_inline

        job_id = str(uuid4())
        mock_job = MagicMock(spec=CrawlJob)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_job)
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()

        # Mock the restaurant upsert + select
        mock_restaurant = MagicMock()
        mock_restaurant.id = uuid4()
        mock_select_result = MagicMock()
        mock_select_result.scalar_one_or_none = MagicMock(return_value=mock_restaurant)
        mock_session.execute = AsyncMock(return_value=mock_select_result)
        mock_session.add = MagicMock()

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=False)

        # Mock crawler returning 2 restaurants
        mock_crawler = MagicMock()
        mock_crawler.run = AsyncMock(return_value=[
            {"name": "Pizza Place", "address": "123 Main St", "city": "Nashville", "state": "TN"},
            {"name": "Taco Shop", "address": "456 Oak Ave", "city": "Nashville", "state": "TN"},
        ])

        with patch("src.api.routers.jobs.async_session", return_value=mock_context), \
             patch("src.tasks.crawl_tasks._get_crawler", return_value=mock_crawler):
            await _run_crawl_inline("google_maps", "restaurants", "Nashville, TN", job_id)

        # Job should be marked completed
        assert mock_job.status == "completed"
        assert mock_job.total_items == 2
        assert mock_job.finished_at is not None

    @pytest.mark.asyncio
    async def test_empty_results_still_completes(self):
        """Crawl returning 0 results should still complete (not fail)."""
        from src.api.routers.jobs import _run_crawl_inline

        job_id = str(uuid4())
        mock_job = MagicMock(spec=CrawlJob)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_job)
        mock_session.commit = AsyncMock()

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=False)

        mock_crawler = MagicMock()
        mock_crawler.run = AsyncMock(return_value=[])

        with patch("src.api.routers.jobs.async_session", return_value=mock_context), \
             patch("src.tasks.crawl_tasks._get_crawler", return_value=mock_crawler):
            await _run_crawl_inline("google_maps", "restaurants", "Empty Town", job_id)

        assert mock_job.status == "completed"
        assert mock_job.total_items == 0

    @pytest.mark.asyncio
    async def test_skips_records_without_name(self):
        """Records without a name should be skipped."""
        from src.api.routers.jobs import _run_crawl_inline

        job_id = str(uuid4())
        mock_job = MagicMock(spec=CrawlJob)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_job)
        mock_session.commit = AsyncMock()

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=False)

        mock_crawler = MagicMock()
        mock_crawler.run = AsyncMock(return_value=[
            {"name": "", "address": "123 Main St"},  # No name — should skip
            {"address": "456 Oak Ave"},  # No name key — should skip
        ])

        with patch("src.api.routers.jobs.async_session", return_value=mock_context), \
             patch("src.tasks.crawl_tasks._get_crawler", return_value=mock_crawler):
            await _run_crawl_inline("google_maps", "restaurants", "Test", job_id)

        assert mock_job.status == "completed"
        assert mock_job.total_items == 0  # Both skipped
