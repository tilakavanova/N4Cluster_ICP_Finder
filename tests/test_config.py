"""Tests for application configuration."""

import pytest
from src.config import Settings


class TestConfig:
    def test_default_values(self):
        s = Settings(database_url="postgresql+asyncpg://localhost/test")
        assert s.db_pool_size == 20
        assert s.crawl_concurrency == 3
        assert s.crawl_retry_attempts == 3
        assert s.rate_limit_per_second == 1.0
        assert s.llm_model == "gpt-4o-mini"

    def test_scoring_weights_sum_to_100(self):
        s = Settings(database_url="postgresql+asyncpg://localhost/test")
        total = s.weight_independent + s.weight_delivery + s.weight_pos + s.weight_density + s.weight_reviews
        assert total == 100.0

    def test_async_database_url_conversion(self):
        s = Settings(database_url="postgres://user:pass@host/db")
        assert s.async_database_url == "postgresql+asyncpg://user:pass@host/db"

    def test_async_database_url_postgresql(self):
        s = Settings(database_url="postgresql://user:pass@host/db")
        assert s.async_database_url == "postgresql+asyncpg://user:pass@host/db"

    def test_async_database_url_already_async(self):
        s = Settings(database_url="postgresql+asyncpg://user:pass@host/db")
        assert s.async_database_url == "postgresql+asyncpg://user:pass@host/db"

    def test_proxy_pool_empty(self):
        s = Settings(database_url="postgresql+asyncpg://localhost/test", proxy_list="")
        assert s.proxy_pool == []

    def test_proxy_pool_multiple(self):
        s = Settings(database_url="postgresql+asyncpg://localhost/test", proxy_list="http://p1,http://p2")
        assert s.proxy_pool == ["http://p1", "http://p2"]

    def test_default_api_keys_empty(self):
        s = Settings(database_url="postgresql+asyncpg://localhost/test")
        assert s.openai_api_key == ""
        assert s.yelp_fusion_api_key == ""
        assert s.google_places_api_key == ""
