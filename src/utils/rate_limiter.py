"""Per-client Redis sliding-window rate limiter (NIF-255).

Uses a sorted set keyed by client_id.  Each request adds the current
timestamp as both score and member; entries older than the window are
pruned before counting.  Falls back to allowing the request when Redis
is unavailable (fail-open).
"""

import time

import redis

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("rate_limiter")

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def check_rate_limit(
    client_id: str,
    limit: int,
    window_seconds: int = 60,
    *,
    redis_client: redis.Redis | None = None,
) -> tuple[bool, int, int]:
    """Check whether *client_id* is within its rate limit.

    Args:
        client_id: Unique identifier for the API client.
        limit: Maximum number of requests allowed in the window.
        window_seconds: Sliding-window duration in seconds (default 60).
        redis_client: Optional injected client (for testing).

    Returns:
        A tuple of (allowed, remaining, reset_at) where:
          - allowed: True if the request should proceed.
          - remaining: How many requests are left in the current window.
          - reset_at: Unix timestamp when the oldest entry expires (window
            resets).  Returns ``now + window_seconds`` when the bucket is
            empty.
    """
    now = time.time()
    window_start = now - window_seconds
    reset_at = int(now) + window_seconds

    try:
        client = redis_client or _get_redis()
        key = f"ratelimit:{client_id}"

        pipe = client.pipeline()
        # Remove entries older than the window
        pipe.zremrangebyscore(key, "-inf", window_start)
        # Add the current request (score = timestamp, member = unique float
        # string to avoid collisions when multiple requests share a second)
        member = f"{now:.6f}"
        pipe.zadd(key, {member: now})
        # Count requests in the window (includes the one just added)
        pipe.zcard(key)
        # Retrieve oldest entry to calculate reset_at
        pipe.zrange(key, 0, 0, withscores=True)
        # Set TTL so keys self-expire
        pipe.expire(key, window_seconds + 1)
        results = pipe.execute()

        current_count: int = results[2]
        oldest: list = results[3]

        if oldest:
            oldest_ts = oldest[0][1]
            reset_at = int(oldest_ts) + window_seconds

        allowed = current_count <= limit
        remaining = max(0, limit - current_count)

        if not allowed:
            logger.warning(
                "rate_limit_exceeded",
                client_id=client_id,
                count=current_count,
                limit=limit,
            )

        return allowed, remaining, reset_at

    except redis.RedisError as exc:
        # Fail-open: let the request through but log the problem
        logger.warning(
            "rate_limit_redis_unavailable",
            client_id=client_id,
            error=str(exc),
        )
        return True, limit, int(now) + window_seconds
