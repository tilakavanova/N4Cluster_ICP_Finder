"""Tests for application configuration."""

import pytest
from unittest.mock import patch
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
        total = (
            s.weight_independent + s.weight_platform_dependency + s.weight_pos
            + s.weight_density + s.weight_volume + s.weight_cuisine_fit
            + s.weight_price_point + s.weight_engagement
        )
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
        with patch.dict("os.environ", {
            "OPENAI_API_KEY": "",
            "YELP_FUSION_API_KEY": "",
            "GOOGLE_PLACES_API_KEY": "",
        }):
            s = Settings(database_url="postgresql+asyncpg://localhost/test")
        assert s.openai_api_key == ""
        assert s.yelp_fusion_api_key == ""
        assert s.google_places_api_key == ""

    # NIF-219 SendGrid config
    def test_sendgrid_defaults(self):
        s = Settings(database_url="postgresql+asyncpg://localhost/test")
        assert s.sendgrid_api_key == ""
        assert s.sendgrid_from_email == ""
        assert s.sendgrid_from_name == "N4Cluster"
        assert s.sendgrid_webhook_signing_key == ""

    def test_sendgrid_values_from_env(self):
        s = Settings(
            database_url="postgresql+asyncpg://localhost/test",
            sendgrid_api_key="SG.test_key_123",
            sendgrid_from_email="hello@n4cluster.com",
            sendgrid_from_name="N4Cluster Outreach",
            sendgrid_webhook_signing_key="MFkwEwYHKoZIzj0CAQY=",
        )
        assert s.sendgrid_api_key == "SG.test_key_123"
        assert s.sendgrid_from_email == "hello@n4cluster.com"
        assert s.sendgrid_from_name == "N4Cluster Outreach"
        assert s.sendgrid_webhook_signing_key == "MFkwEwYHKoZIzj0CAQY="
