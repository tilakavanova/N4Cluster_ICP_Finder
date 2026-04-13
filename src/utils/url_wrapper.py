"""URL wrapping utilities for click-redirect and open-pixel tracking (NIF-223).

Wraps original URLs into tracking URLs and generates 1×1 pixel URLs.
"""

import redis

from src.utils.tracking_tokens import generate_tracking_token, store_tracking_token


def wrap_url(
    original_url: str,
    lead_id: str,
    campaign_id: str,
    target_id: str,
    channel: str,
    base_url: str,
    *,
    redis_client: redis.Redis | None = None,
) -> str:
    """Wrap an original URL into a tracking redirect URL.

    Generates a token, stores token→data in Redis, and returns
    the tracking URL in the form ``{base_url}/t/{token}``.

    Args:
        original_url: The destination URL to redirect to.
        lead_id: UUID of the lead (string).
        campaign_id: UUID of the campaign (string).
        target_id: UUID of the outreach target (string).
        channel: Delivery channel, e.g. "email".
        base_url: Base URL of this service, e.g. "https://track.example.com".
        redis_client: Optional injected Redis client (for testing).

    Returns:
        Tracking URL string.
    """
    token = generate_tracking_token()
    data = {
        "url": original_url,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "channel": channel,
        "target_id": target_id,
    }
    store_tracking_token(token, data, redis_client=redis_client)
    return f"{base_url}/t/{token}"


def generate_pixel_url(
    lead_id: str,
    campaign_id: str,
    target_id: str,
    base_url: str,
    *,
    redis_client: redis.Redis | None = None,
) -> str:
    """Generate a 1×1 open-pixel tracking URL.

    Args:
        lead_id: UUID of the lead (string).
        campaign_id: UUID of the campaign (string).
        target_id: UUID of the outreach target (string).
        base_url: Base URL of this service.
        redis_client: Optional injected Redis client (for testing).

    Returns:
        Pixel URL string ending in ``.gif``.
    """
    token = generate_tracking_token()
    data = {
        "url": None,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "channel": "email",
        "target_id": target_id,
    }
    store_tracking_token(token, data, redis_client=redis_client)
    return f"{base_url}/px/{token}.gif"
