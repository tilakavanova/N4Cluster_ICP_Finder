"""Tracking token utilities for click/open tracking (NIF-223).

Generates short URL-safe tokens, stores token→data mappings in Redis
with a 30-day TTL, and retrieves data by token.
"""

import json
import secrets

import redis

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("tracking_tokens")

# Default TTL: 30 days in seconds
DEFAULT_TTL = 2_592_000

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def generate_tracking_token() -> str:
    """Generate an 8-character URL-safe tracking token."""
    return secrets.token_urlsafe(8)


def store_tracking_token(
    token: str,
    data: dict,
    ttl: int = DEFAULT_TTL,
    *,
    redis_client: redis.Redis | None = None,
) -> None:
    """Store token → data mapping in Redis.

    Args:
        token: The URL-safe token string.
        data: Dict with keys: url, lead_id, campaign_id, channel, target_id.
        ttl: Expiry in seconds (default 30 days).
        redis_client: Optional injected client (for testing).
    """
    client = redis_client or _get_redis()
    key = f"track:{token}"
    client.setex(key, ttl, json.dumps(data))
    logger.debug("tracking_token_stored", token=token, ttl=ttl)


def get_tracking_data(
    token: str,
    *,
    redis_client: redis.Redis | None = None,
) -> dict | None:
    """Retrieve tracking data from Redis by token.

    Returns:
        The stored data dict, or None if token is missing / expired.
    """
    client = redis_client or _get_redis()
    key = f"track:{token}"
    raw = client.get(key)
    if raw is None:
        logger.debug("tracking_token_miss", token=token)
        return None
    return json.loads(raw)
