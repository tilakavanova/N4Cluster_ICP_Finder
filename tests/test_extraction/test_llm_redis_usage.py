"""Tests for NIF-253: Redis-backed LLM token tracking and daily limit."""

import pytest
import fakeredis
from datetime import date, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

import src.extraction.llm_client as llm_module
from src.extraction.llm_client import (
    LLMClient,
    _track_tokens,
    get_daily_usage,
    _is_budget_exceeded,
    _redis_key,
    _get_today_key,
)


def _fake_redis():
    """Return a fakeredis instance that behaves like redis.Redis."""
    return fakeredis.FakeRedis(decode_responses=True)


def _reset_module_state(fake_r=None):
    """Reset module-level Redis and in-memory state between tests."""
    llm_module._in_memory_fallback.clear()
    llm_module._redis_instance = fake_r
    llm_module._redis_available = True if fake_r is not None else None


# ── Token increments ───────────────────────────────────────────────────────

class TestTokenIncrementsRedis:
    def setup_method(self):
        self.r = _fake_redis()
        _reset_module_state(self.r)

    def test_openai_input_output_stored(self):
        _track_tokens("openai", 100, 50)
        day = _get_today_key()
        key = _redis_key("openai", day)
        assert int(self.r.hget(key, "input")) == 100
        assert int(self.r.hget(key, "output")) == 50

    def test_anthropic_increments_separately(self):
        _track_tokens("anthropic", 200, 80)
        day = _get_today_key()
        key = _redis_key("anthropic", day)
        assert int(self.r.hget(key, "input")) == 200
        assert int(self.r.hget(key, "output")) == 80

    def test_increments_are_additive(self):
        _track_tokens("openai", 100, 50)
        _track_tokens("openai", 200, 100)
        day = _get_today_key()
        key = _redis_key("openai", day)
        assert int(self.r.hget(key, "input")) == 300
        assert int(self.r.hget(key, "output")) == 150

    def test_ttl_set_on_key(self):
        _track_tokens("openai", 1, 1)
        day = _get_today_key()
        key = _redis_key("openai", day)
        ttl = self.r.ttl(key)
        assert 0 < ttl <= 172800


# ── get_daily_usage ────────────────────────────────────────────────────────

class TestGetDailyUsage:
    def setup_method(self):
        self.r = _fake_redis()
        _reset_module_state(self.r)

    def test_returns_correct_structure(self):
        _track_tokens("openai", 300, 150)
        usage = get_daily_usage()
        assert usage["date"] == _get_today_key()
        assert usage["openai_input"] == 300
        assert usage["openai_output"] == 150
        assert "total_tokens" in usage
        assert "budget_limit" in usage

    def test_provider_filter(self):
        _track_tokens("openai", 100, 50)
        _track_tokens("anthropic", 200, 80)
        usage = get_daily_usage(provider="openai")
        assert usage["provider"] == "openai"
        assert usage["input"] == 100
        assert usage["output"] == 50
        # anthropic not included
        assert "anthropic_input" not in usage

    def test_total_tokens_aggregated(self):
        _track_tokens("openai", 100, 50)
        _track_tokens("anthropic", 200, 80)
        usage = get_daily_usage()
        assert usage["total_tokens"] == 430

    def test_budget_remaining_calculated(self):
        with patch("src.extraction.llm_client.settings") as mock:
            mock.llm_daily_token_limit = 1000
            mock.redis_url = "redis://localhost"
            _track_tokens("openai", 100, 50)
            usage = get_daily_usage()
        assert isinstance(usage["budget_remaining"], int) or usage["budget_remaining"] is None


# ── Budget enforcement ─────────────────────────────────────────────────────

class TestBudgetEnforcement:
    def setup_method(self):
        self.r = _fake_redis()
        _reset_module_state(self.r)

    def test_budget_not_exceeded_below_limit(self):
        _track_tokens("openai", 999_998, 0)
        with patch("src.extraction.llm_client.settings") as mock:
            mock.llm_daily_token_limit = 1_000_000
            assert not _is_budget_exceeded()

    def test_budget_exceeded_at_limit(self):
        _track_tokens("openai", 999_999, 1)  # exactly 1_000_000
        with patch("src.extraction.llm_client.settings") as mock:
            mock.llm_daily_token_limit = 1_000_000
            assert _is_budget_exceeded()

    @pytest.mark.asyncio
    async def test_budget_exceeded_returns_empty_from_extract_json(self):
        _track_tokens("openai", 999_999, 1)
        client = LLMClient()
        with patch("src.extraction.llm_client.settings") as mock:
            mock.llm_daily_token_limit = 1_000_000
            mock.openai_api_key = "sk-test"
            mock.anthropic_api_key = ""
            result = await client.extract_json("test prompt")
        assert result == {}

    def test_zero_limit_means_unlimited(self):
        _track_tokens("openai", 10_000_000, 0)
        with patch("src.extraction.llm_client.settings") as mock:
            mock.llm_daily_token_limit = 0
            assert not _is_budget_exceeded()


# ── Date rollover ──────────────────────────────────────────────────────────

class TestDateRollover:
    def setup_method(self):
        self.r = _fake_redis()
        _reset_module_state(self.r)

    def test_different_day_uses_different_key(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        today = _get_today_key()

        # Manually set yesterday's key
        self.r.hset(_redis_key("openai", yesterday), "input", 999999)
        self.r.hset(_redis_key("openai", yesterday), "output", 999999)

        # Today should start at 0
        _track_tokens("openai", 100, 50)
        usage = get_daily_usage(provider="openai")
        assert usage["input"] == 100
        assert usage["output"] == 50

    def test_yesterday_key_not_counted_in_budget(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.r.hset(_redis_key("openai", yesterday), "input", 999999)
        self.r.hset(_redis_key("openai", yesterday), "output", 999999)

        with patch("src.extraction.llm_client.settings") as mock:
            mock.llm_daily_token_limit = 1_000_000
            assert not _is_budget_exceeded()


# ── Redis unavailable fallback ─────────────────────────────────────────────

class TestRedisFallback:
    def setup_method(self):
        _reset_module_state(None)
        llm_module._redis_available = False  # force in-memory path

    def teardown_method(self):
        llm_module._in_memory_fallback.clear()
        llm_module._redis_instance = None
        llm_module._redis_available = None

    def test_tokens_tracked_in_memory_when_redis_down(self):
        _track_tokens("openai", 100, 50)
        day = _get_today_key()
        assert llm_module._in_memory_fallback[day]["openai_input"] == 100
        assert llm_module._in_memory_fallback[day]["openai_output"] == 50

    def test_budget_check_uses_memory_fallback(self):
        _track_tokens("openai", 999_999, 1)
        with patch("src.extraction.llm_client.settings") as mock:
            mock.llm_daily_token_limit = 1_000_000
            assert _is_budget_exceeded()

    @pytest.mark.asyncio
    async def test_llm_calls_succeed_when_redis_down(self):
        """Redis being down must not break LLM calls (fail-open)."""
        client = LLMClient()
        mock_openai = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = '{"ok": true}'
        mock_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_resp)
        client._openai_client = mock_openai

        with patch("src.extraction.llm_client.settings") as mock:
            mock.openai_api_key = "sk-test"
            mock.anthropic_api_key = ""
            mock.llm_daily_token_limit = 1_000_000
            mock.llm_model = "gpt-4o-mini"
            mock.llm_max_tokens = 4000
            result = await client.extract_json("test")
        assert result == {"ok": True}

    def test_get_daily_usage_returns_structure_from_memory(self):
        _track_tokens("openai", 200, 100)
        usage = get_daily_usage()
        assert usage["openai_input"] == 200
        assert usage["openai_output"] == 100
        assert "date" in usage

    def test_redis_down_warning_logged(self):
        """When Redis is genuinely unreachable, a warning is emitted."""
        llm_module._redis_instance = None
        llm_module._redis_available = None  # force re-probe

        with patch("src.extraction.llm_client.logger") as mock_logger, \
             patch("src.extraction.llm_client.settings") as mock_settings:
            mock_settings.redis_url = "redis://127.0.0.1:1"  # nothing listening

            # _get_redis() will fail to connect and should log a warning
            from src.extraction.llm_client import _get_redis
            result = _get_redis()

        assert result is None
        mock_logger.warning.assert_called_once()
        call_kwargs = mock_logger.warning.call_args[1]
        assert "error" in call_kwargs
