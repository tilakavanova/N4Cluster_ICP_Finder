"""Tests for LLM-powered dynamic email content service (NIF-265).

Covers:
- generate_email_content with LLM response
- generate_email_content fallback on LLM failure
- archetype caching hit/miss
- cache expiry
- personalisation token replacement
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.services.dynamic_email import (
    generate_email_content,
    get_cached_archetype,
    set_cached_archetype,
    clear_archetype_cache,
    _archetype_key,
    _personalise,
    _archetype_cache,
    CACHE_TTL_SECONDS,
)


class TestArchetypeCache:
    def setup_method(self):
        clear_archetype_cache()

    def test_cache_key_deterministic(self):
        """Same inputs produce the same cache key."""
        key1 = _archetype_key("Pizza", "New York")
        key2 = _archetype_key("pizza", "new york")
        assert key1 == key2

    def test_cache_key_different_inputs(self):
        """Different inputs produce different keys."""
        key1 = _archetype_key("Pizza", "New York")
        key2 = _archetype_key("Sushi", "Chicago")
        assert key1 != key2

    def test_set_and_get_cached(self):
        """Cache stores and retrieves archetype content."""
        content = {"subject": "Test", "body": "Hello"}
        set_cached_archetype("Pizza", "NYC", content)
        cached = get_cached_archetype("Pizza", "NYC")
        assert cached == content

    def test_cache_miss(self):
        """Returns None for uncached archetype."""
        assert get_cached_archetype("Unknown", "Nowhere") is None

    def test_cache_expiry(self):
        """Expired cache entries return None."""
        key = _archetype_key("Pizza", "NYC")
        _archetype_cache[key] = {
            "content": {"subject": "Old", "body": "Stale"},
            "created_at": datetime.now(timezone.utc) - timedelta(seconds=CACHE_TTL_SECONDS + 10),
        }
        assert get_cached_archetype("Pizza", "NYC") is None

    def test_clear_cache(self):
        """Clearing cache removes all entries."""
        set_cached_archetype("Pizza", "NYC", {"subject": "A"})
        set_cached_archetype("Sushi", "LA", {"subject": "B"})
        count = clear_archetype_cache()
        assert count == 2
        assert get_cached_archetype("Pizza", "NYC") is None


class TestPersonalise:
    def test_token_replacement(self):
        content = {"subject": "Hi {first_name}", "body": "We help {company} in {city}"}
        lead = {"first_name": "Mario", "company": "Mario's Pizzeria", "city": "Brooklyn"}
        result = _personalise(content, lead)
        assert result["subject"] == "Hi Mario"
        assert "Mario's Pizzeria" in result["body"]
        assert "Brooklyn" in result["body"]

    def test_missing_lead_fields(self):
        content = {"subject": "Hi {first_name}", "body": "Hello {company}"}
        lead = {}
        result = _personalise(content, lead)
        assert result["subject"] == "Hi there"
        assert "your restaurant" in result["body"]


class TestGenerateEmailContent:
    @pytest.mark.asyncio
    async def test_generate_with_llm(self):
        """Successful LLM-generated email content."""
        clear_archetype_cache()
        lead = {"first_name": "Tony", "company": "Tony's Tacos", "city": "Austin", "business_type": "Mexican"}
        llm_response = {"subject": "Grow {company}", "body": "Hi {first_name}, we help in {city}"}

        with patch("src.services.dynamic_email.llm_client") as mock_client:
            mock_client.extract_json = AsyncMock(return_value=llm_response)
            result = await generate_email_content(lead)

        assert "Tony's Tacos" in result["subject"]
        assert "Tony" in result["body"]
        assert "Austin" in result["body"]

    @pytest.mark.asyncio
    async def test_generate_uses_cache(self):
        """Second call for same archetype uses cache, not LLM."""
        clear_archetype_cache()
        lead = {"first_name": "A", "company": "B", "city": "NYC", "business_type": "Pizza"}
        set_cached_archetype("Pizza", "NYC", {"subject": "Cached", "body": "Cached body"})

        with patch("src.services.dynamic_email.llm_client") as mock_client:
            mock_client.extract_json = AsyncMock()
            result = await generate_email_content(lead)

        mock_client.extract_json.assert_not_called()
        assert result["subject"] == "Cached"

    @pytest.mark.asyncio
    async def test_generate_fallback_on_error(self):
        """Falls back to template when LLM fails."""
        clear_archetype_cache()
        lead = {"first_name": "Sal", "company": "Sal's Deli", "city": "Philly", "business_type": "Deli"}

        with patch("src.services.dynamic_email.llm_client") as mock_client:
            mock_client.extract_json = AsyncMock(side_effect=Exception("LLM down"))
            result = await generate_email_content(lead)

        assert "Sal's Deli" in result["subject"]
        assert "Sal" in result["body"]
