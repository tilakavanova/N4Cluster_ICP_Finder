"""Unified LLM client with OpenAI primary and Claude fallback + cost tracking."""

import json
from datetime import date
from typing import Any

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("llm_client")

# In-memory fallback used when Redis is unavailable
_in_memory_fallback: dict[str, dict[str, int]] = {}

# Lazy-initialized Redis client; None means not yet attempted, False means unavailable
_redis_instance: Any = None
_redis_available: bool | None = None  # None = not yet probed


def _get_redis() -> Any:
    """Return a connected sync Redis client, or None if unavailable."""
    global _redis_instance, _redis_available
    if _redis_available is False:
        return None
    if _redis_instance is not None:
        return _redis_instance
    try:
        import redis as _redis_lib  # provided by celery[redis]
        client = _redis_lib.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
        _redis_instance = client
        _redis_available = True
        return client
    except Exception as exc:
        logger.warning("redis_unavailable_llm_tracking_using_memory", error=str(exc))
        _redis_available = False
        return None


def _get_today_key() -> str:
    return date.today().isoformat()


def _redis_key(provider: str, day: str) -> str:
    return f"llm_usage:{provider}:{day}"


def _track_tokens(provider: str, input_tokens: int, output_tokens: int) -> None:
    """Atomically increment daily token counters for *provider* in Redis (or memory fallback)."""
    day = _get_today_key()
    r = _get_redis()
    if r is not None:
        try:
            key = _redis_key(provider, day)
            r.hincrby(key, "input", input_tokens)
            r.hincrby(key, "output", output_tokens)
            r.expire(key, 172800)  # 2-day TTL so keys self-clean
            total_input = int(r.hget(key, "input") or 0)
            total_output = int(r.hget(key, "output") or 0)
            logger.info(
                "llm_tokens_used",
                provider=provider,
                input=input_tokens,
                output=output_tokens,
                daily_total=total_input + total_output,
            )
            return
        except Exception as exc:
            logger.warning("redis_track_failed_falling_back_to_memory", error=str(exc))

    # ── in-memory fallback ─────────────────────────────────────────────────
    if day not in _in_memory_fallback:
        _in_memory_fallback.clear()  # drop stale days
        _in_memory_fallback[day] = {
            "openai_input": 0, "openai_output": 0,
            "anthropic_input": 0, "anthropic_output": 0,
        }
    _in_memory_fallback[day][f"{provider}_input"] += input_tokens
    _in_memory_fallback[day][f"{provider}_output"] += output_tokens
    total = (
        _in_memory_fallback[day][f"{provider}_input"]
        + _in_memory_fallback[day][f"{provider}_output"]
    )
    logger.info(
        "llm_tokens_used",
        provider=provider,
        input=input_tokens,
        output=output_tokens,
        daily_total=total,
    )


def get_daily_usage(provider: str | None = None) -> dict[str, Any]:
    """Return today's token usage.

    If *provider* is given, returns usage only for that provider.
    Otherwise returns aggregate across all providers (backward-compatible).
    """
    day = _get_today_key()
    providers = [provider] if provider else ["openai", "anthropic"]

    usage: dict[str, int] = {}
    r = _get_redis()

    for prov in providers:
        if r is not None:
            try:
                key = _redis_key(prov, day)
                raw = r.hgetall(key)
                inp = int(raw.get("input", 0))
                out = int(raw.get("output", 0))
            except Exception:
                inp, out = 0, 0
        else:
            mem = _in_memory_fallback.get(day, {})
            inp = mem.get(f"{prov}_input", 0)
            out = mem.get(f"{prov}_output", 0)

        if provider:
            usage = {"input": inp, "output": out}
        else:
            usage[f"{prov}_input"] = inp
            usage[f"{prov}_output"] = out

    total = sum(usage.values())
    result: dict[str, Any] = {
        "date": day,
        **usage,
        "total_tokens": total,
        "budget_limit": settings.llm_daily_token_limit,
        "budget_remaining": (
            max(0, settings.llm_daily_token_limit - total)
            if settings.llm_daily_token_limit > 0
            else None
        ),
    }
    if provider:
        result["provider"] = provider
    return result


def _is_budget_exceeded() -> bool:
    """Return True if the configured daily token limit has been reached."""
    if settings.llm_daily_token_limit <= 0:
        return False
    day = _get_today_key()
    r = _get_redis()
    total = 0
    for prov in ("openai", "anthropic"):
        if r is not None:
            try:
                key = _redis_key(prov, day)
                raw = r.hgetall(key)
                total += int(raw.get("input", 0)) + int(raw.get("output", 0))
            except Exception:
                pass
        else:
            mem = _in_memory_fallback.get(day, {})
            total += mem.get(f"{prov}_input", 0) + mem.get(f"{prov}_output", 0)
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
        usage = response.usage
        if usage:
            _track_tokens("anthropic", usage.input_tokens, usage.output_tokens)
        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)


llm_client = LLMClient()
