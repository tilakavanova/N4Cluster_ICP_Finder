"""Unified LLM client with OpenAI primary and Claude fallback + cost tracking."""

import json
from datetime import date
from typing import Any

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("llm_client")

# Daily token usage tracking (resets each day)
_daily_usage: dict[str, dict[str, int]] = {}


def _get_today_key() -> str:
    return date.today().isoformat()


def _track_tokens(provider: str, input_tokens: int, output_tokens: int) -> None:
    """Track token usage per day per provider."""
    day = _get_today_key()
    if day not in _daily_usage:
        _daily_usage.clear()  # Reset old days
        _daily_usage[day] = {"openai_input": 0, "openai_output": 0, "anthropic_input": 0, "anthropic_output": 0}
    _daily_usage[day][f"{provider}_input"] += input_tokens
    _daily_usage[day][f"{provider}_output"] += output_tokens
    total = _daily_usage[day][f"{provider}_input"] + _daily_usage[day][f"{provider}_output"]
    logger.info("llm_tokens_used", provider=provider, input=input_tokens, output=output_tokens, daily_total=total)


def get_daily_usage() -> dict[str, Any]:
    """Return today's token usage for monitoring."""
    day = _get_today_key()
    usage = _daily_usage.get(day, {"openai_input": 0, "openai_output": 0, "anthropic_input": 0, "anthropic_output": 0})
    return {
        "date": day,
        **usage,
        "total_tokens": sum(usage.values()),
        "budget_limit": settings.llm_daily_token_limit,
        "budget_remaining": max(0, settings.llm_daily_token_limit - sum(usage.values())) if settings.llm_daily_token_limit > 0 else None,
    }


def _is_budget_exceeded() -> bool:
    """Check if daily token budget is exceeded."""
    if settings.llm_daily_token_limit <= 0:
        return False  # No limit configured
    day = _get_today_key()
    usage = _daily_usage.get(day, {})
    total = sum(usage.values())
    return total >= settings.llm_daily_token_limit


class LLMClient:
    """Unified async LLM client with provider fallback and cost tracking."""

    def __init__(self):
        self._openai_client = None
        self._anthropic_client = None

    def _get_openai(self):
        if self._openai_client is None and settings.openai_api_key:
            from openai import AsyncOpenAI
            self._openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._openai_client

    def _get_anthropic(self):
        if self._anthropic_client is None and settings.anthropic_api_key:
            from anthropic import AsyncAnthropic
            self._anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._anthropic_client

    async def extract_json(self, prompt: str) -> dict[str, Any]:
        """Send prompt to LLM and parse JSON response. Tries OpenAI first, falls back to Claude."""
        if _is_budget_exceeded():
            logger.warning("llm_daily_budget_exceeded", usage=get_daily_usage())
            return {}

        openai = self._get_openai()
        if openai:
            try:
                return await self._call_openai(openai, prompt)
            except Exception as e:
                logger.warning("openai_failed_falling_back", error=str(e))

        anthropic = self._get_anthropic()
        if anthropic:
            try:
                return await self._call_anthropic(anthropic, prompt)
            except Exception as e:
                logger.error("anthropic_also_failed", error=str(e))
                raise

        raise RuntimeError("No LLM API keys configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY.")

    async def _call_openai(self, client, prompt: str) -> dict:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": "You are a data extraction assistant. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=settings.llm_max_tokens,
            response_format={"type": "json_object"},
        )
        # Track token usage
        usage = response.usage
        if usage:
            _track_tokens("openai", usage.prompt_tokens, usage.completion_tokens)

        text = response.choices[0].message.content
        return json.loads(text)

    async def _call_anthropic(self, client, prompt: str) -> dict:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=settings.llm_max_tokens,
            messages=[{"role": "user", "content": prompt}],
            system="You are a data extraction assistant. Return only valid JSON, no markdown fencing.",
        )
        # Track token usage
        usage = response.usage
        if usage:
            _track_tokens("anthropic", usage.input_tokens, usage.output_tokens)

        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)


llm_client = LLMClient()
