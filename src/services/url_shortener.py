"""SMS URL shortening and click-tracking service (NIF-233).

Generates short tracking URLs (``/t/s/{token}``) for SMS messages.  When a
recipient clicks the short URL the tracking router logs a TrackerEvent with
channel="sms" and event_type="click", then redirects to the original URL.
"""

from __future__ import annotations

import re

import redis as redis_lib

from src.config import settings
from src.utils.logging import get_logger
from src.utils.tracking_tokens import generate_tracking_token, store_tracking_token

logger = get_logger("url_shortener")

# Match http(s) URLs in plain text (SMS bodies are plain text, not HTML)
_URL_RE = re.compile(r'(https?://[^\s<>"]+)', re.IGNORECASE)


def shorten_url(
    original_url: str,
    lead_id: str,
    campaign_id: str,
    target_id: str = "",
    *,
    base_url: str | None = None,
    redis_client: redis_lib.Redis | None = None,
) -> str:
    """Generate a short tracking URL that redirects to *original_url*.

    The token is stored in Redis via the existing tracking-token infra
    with ``channel="sms"`` so the redirect handler can distinguish SMS
    clicks from email clicks.

    Returns:
        A short URL string, e.g. ``https://n4cluster.com/t/s/{token}``.
    """
    base = base_url or settings.tracking_base_url
    token = generate_tracking_token()
    data = {
        "url": original_url,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "target_id": target_id,
        "channel": "sms",
    }
    store_tracking_token(token, data, redis_client=redis_client)
    short_url = f"{base}/t/s/{token}"
    logger.debug("url_shortened", original=original_url, short=short_url)
    return short_url


def replace_urls_in_message(
    message: str,
    lead_id: str,
    campaign_id: str,
    target_id: str = "",
    *,
    base_url: str | None = None,
    redis_client: redis_lib.Redis | None = None,
) -> str:
    """Replace all URLs in an SMS message body with shortened tracking URLs.

    Returns:
        The message with all URLs replaced.
    """
    def _replace(match: re.Match) -> str:
        original = match.group(1)
        return shorten_url(
            original,
            lead_id=lead_id,
            campaign_id=campaign_id,
            target_id=target_id,
            base_url=base_url,
            redis_client=redis_client,
        )

    return _URL_RE.sub(_replace, message)
