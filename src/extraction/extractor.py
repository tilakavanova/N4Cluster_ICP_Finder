"""Data extraction pipeline — raw crawl data to structured records."""

from typing import Any

from src.config import settings
from src.extraction.llm_client import llm_client
from src.extraction.prompts import (
    RESTAURANT_EXTRACTION_PROMPT,
    CHAIN_DETECTION_PROMPT,
    POS_DETECTION_PROMPT,
)
from src.utils.logging import get_logger

logger = get_logger("extractor")


def _max_llm_chars() -> int:
    """Max chars to send to LLM (rough token estimate: 1 token ~ 4 chars)."""
    return settings.llm_max_tokens * 4


class RestaurantExtractor:
    """Extract structured restaurant data from raw crawl output."""

    async def extract_from_text(self, raw_text: str) -> dict[str, Any]:
        """Extract restaurant data from raw page text using LLM."""
        if not raw_text or len(raw_text.strip()) < 50:
            logger.warning("text_too_short_for_extraction", length=len(raw_text))
            return {}

        truncated = raw_text[:_max_llm_chars()]
        prompt = RESTAURANT_EXTRACTION_PROMPT.format(text=truncated)

        try:
            result = await llm_client.extract_json(prompt)
            logger.info("extraction_success", fields=list(result.keys()))
            return result
        except Exception as e:
            logger.error("extraction_failed", error=str(e))
            return {}

    async def detect_chain(self, name: str, address: str = "", context: str = "") -> dict:
        """Detect if a restaurant is part of a chain."""
        prompt = CHAIN_DETECTION_PROMPT.format(name=name, address=address, context=context)
        try:
            return await llm_client.extract_json(prompt)
        except Exception as e:
            logger.error("chain_detection_failed", error=str(e))
            return {"is_chain": False, "confidence": 0.0}

    async def detect_pos(self, raw_text: str) -> dict:
        """Detect POS system from website text."""
        if not raw_text:
            return {"has_pos": False, "confidence": 0.0}

        truncated = raw_text[:8000]
        prompt = POS_DETECTION_PROMPT.format(text=truncated)

        try:
            return await llm_client.extract_json(prompt)
        except Exception as e:
            logger.error("pos_detection_failed", error=str(e))
            return {"has_pos": False, "confidence": 0.0}

    async def extract_and_enrich(self, raw_data: dict) -> dict[str, Any]:
        """Full extraction pipeline: extract base data, detect chain, detect POS."""
        result = {}

        raw_text = raw_data.get("raw_text", "")
        if raw_text:
            result = await self.extract_from_text(raw_text)

        # Merge structured data from crawlers
        for key in ["name", "address", "city", "state", "phone", "rating", "review_count"]:
            if key in raw_data and raw_data[key] and not result.get(key):
                result[key] = raw_data[key]

        # Chain detection
        name = result.get("name") or raw_data.get("name", "")
        if name:
            chain_info = await self.detect_chain(
                name=name,
                address=result.get("address", ""),
                context=str(result.get("cuisine_type", "")),
            )
            result["is_chain"] = chain_info.get("is_chain", False)
            result["chain_name"] = chain_info.get("chain_name")

        # POS detection
        if raw_text:
            pos_info = await self.detect_pos(raw_text)
            result["has_pos"] = pos_info.get("has_pos", False)
            result["pos_provider"] = pos_info.get("pos_provider")

        return result


extractor = RestaurantExtractor()
