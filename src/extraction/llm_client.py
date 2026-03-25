"""Unified LLM client with OpenAI primary and Claude fallback."""

import json
from typing import Any

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("llm_client")


class LLMClient:
    """Unified async LLM client with provider fallback."""

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
        text = response.choices[0].message.content
        return json.loads(text)

    async def _call_anthropic(self, client, prompt: str) -> dict:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=settings.llm_max_tokens,
            messages=[{"role": "user", "content": prompt}],
            system="You are a data extraction assistant. Return only valid JSON, no markdown fencing.",
        )
        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)


llm_client = LLMClient()
