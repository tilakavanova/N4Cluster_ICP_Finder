"""LLM-powered dynamic email content with archetype caching (NIF-265).

Generates personalised email content using the LLM client, caching results
by archetype (restaurant_type + city combination) so identical archetypes
re-use the same content skeleton and only swap in lead-specific tokens.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from src.extraction.llm_client import llm_client
from src.utils.logging import get_logger

logger = get_logger("dynamic_email")

# ── In-memory archetype cache (upgraded to Redis when available) ──

_archetype_cache: dict[str, dict[str, Any]] = {}

# TTL for cached archetypes in seconds (default 24 hours)
CACHE_TTL_SECONDS = 86_400


def _archetype_key(restaurant_type: str, city: str) -> str:
    """Deterministic cache key for an archetype."""
    raw = f"{restaurant_type.lower().strip()}:{city.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _is_cache_valid(entry: dict[str, Any]) -> bool:
    if "created_at" not in entry:
        return False
    age = (datetime.now(timezone.utc) - entry["created_at"]).total_seconds()
    return age < CACHE_TTL_SECONDS


def get_cached_archetype(restaurant_type: str, city: str) -> dict[str, Any] | None:
    """Return cached archetype content if available and not expired."""
    key = _archetype_key(restaurant_type, city)
    entry = _archetype_cache.get(key)
    if entry and _is_cache_valid(entry):
        logger.debug("archetype_cache_hit", key=key)
        return entry["content"]
    return None


def set_cached_archetype(restaurant_type: str, city: str, content: dict[str, Any]) -> None:
    """Cache archetype content."""
    key = _archetype_key(restaurant_type, city)
    _archetype_cache[key] = {
        "content": content,
        "created_at": datetime.now(timezone.utc),
    }
    logger.debug("archetype_cached", key=key)


def clear_archetype_cache() -> int:
    """Clear the entire archetype cache. Returns number of entries cleared."""
    count = len(_archetype_cache)
    _archetype_cache.clear()
    return count


async def generate_email_content(
    lead: dict[str, Any],
    template_name: str = "default",
    archetype: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Generate personalised email content using LLM.

    Args:
        lead: Lead data dict with keys like first_name, company, business_type, city.
        template_name: Template name hint for the LLM prompt.
        archetype: Optional pre-built archetype dict; if None, derived from lead.

    Returns:
        Dict with 'subject' and 'body' keys.
    """
    restaurant_type = (archetype or {}).get("restaurant_type") or lead.get("business_type", "restaurant")
    city = (archetype or {}).get("city") or lead.get("city", "your area")

    # Check archetype cache first
    cached = get_cached_archetype(restaurant_type, city)
    if cached:
        # Personalise the cached skeleton with lead-specific tokens
        return _personalise(cached, lead)

    # Build the LLM prompt
    prompt = (
        f"Write a short sales email for a {restaurant_type} in {city}. "
        f"Template style: {template_name}. "
        f"The recipient is {lead.get('first_name', 'there')} at {lead.get('company', 'their restaurant')}. "
        "Return JSON with keys 'subject' and 'body'. Keep it under 150 words. "
        "Be professional and concise."
    )

    try:
        content = await llm_client.extract_json(prompt)
        if not content or "subject" not in content:
            content = {"subject": f"Grow your {restaurant_type} in {city}", "body": content.get("body", "")}

        # Cache the archetype skeleton (before personalisation)
        set_cached_archetype(restaurant_type, city, content)

        return _personalise(content, lead)

    except Exception as exc:
        logger.error("dynamic_email_generation_failed", error=str(exc))
        # Fallback content
        return {
            "subject": f"Partnership opportunity for {lead.get('company', 'your restaurant')}",
            "body": (
                f"Hi {lead.get('first_name', 'there')},\n\n"
                f"We help {restaurant_type}s in {city} grow their delivery business. "
                "Would you be open to a quick chat?\n\nBest regards"
            ),
        }


def _personalise(content: dict[str, Any], lead: dict[str, Any]) -> dict[str, str]:
    """Replace generic tokens with lead-specific values."""
    subject = str(content.get("subject", ""))
    body = str(content.get("body", ""))

    replacements = {
        "{first_name}": lead.get("first_name", "there"),
        "{company}": lead.get("company", "your restaurant"),
        "{city}": lead.get("city", "your area"),
    }
    for token, value in replacements.items():
        subject = subject.replace(token, value)
        body = body.replace(token, value)

    return {"subject": subject, "body": body}
