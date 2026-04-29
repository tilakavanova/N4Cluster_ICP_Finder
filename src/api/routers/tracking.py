"""Click-redirect and open-pixel tracking endpoints (NIF-223).

GET /t/{token}        — redirect tracker (queues click event)
GET /px/{token}.gif   — open pixel tracker (queues open event)
"""

import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from src.config import settings
from src.utils.logging import get_logger
from src.utils.tracking_tokens import get_tracking_data

logger = get_logger("tracking_api")

router = APIRouter(tags=["tracking"])

# Minimal 1×1 transparent GIF (35 bytes)
_TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff"
    b"\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(ip.encode()).hexdigest()


def _fallback_url() -> str:
    return getattr(settings, "tracking_fallback_url", "https://n4cluster.com")


@router.get("/t/{token}", include_in_schema=False)
async def click_redirect(token: str, request: Request):
    """Redirect to the original URL and queue a click TrackerEvent."""
    from src.tasks.tracking_tasks import log_tracker_event

    data = get_tracking_data(token)

    if data is None:
        logger.info("click_token_not_found", token=token)
        return RedirectResponse(url=_fallback_url(), status_code=302)

    ip_hash = _hash_ip(request.client.host if request.client else None)
    user_agent = request.headers.get("user-agent")
    occurred_at = datetime.now(timezone.utc).isoformat()

    log_tracker_event.delay(
        event_type="click",
        token=token,
        lead_id=data.get("lead_id"),
        campaign_id=data.get("campaign_id"),
        target_id=data.get("target_id"),
        channel=data.get("channel", "email"),
        ip_hash=ip_hash,
        user_agent=user_agent,
        occurred_at=occurred_at,
    )

    destination = data.get("url") or _fallback_url()
    logger.info("click_redirect", token=token, destination=destination)
    return RedirectResponse(url=destination, status_code=302)


@router.get("/t/s/{token}", include_in_schema=False)
async def sms_click_redirect(token: str, request: Request):
    """Redirect short SMS URL and queue an SMS click TrackerEvent (NIF-233)."""
    from src.tasks.tracking_tasks import log_tracker_event

    data = get_tracking_data(token)

    if data is None:
        logger.info("sms_click_token_not_found", token=token)
        return RedirectResponse(url=_fallback_url(), status_code=302)

    ip_hash = _hash_ip(request.client.host if request.client else None)
    user_agent = request.headers.get("user-agent")
    occurred_at = datetime.now(timezone.utc).isoformat()

    log_tracker_event.delay(
        event_type="click",
        token=token,
        lead_id=data.get("lead_id"),
        campaign_id=data.get("campaign_id"),
        target_id=data.get("target_id"),
        channel=data.get("channel", "sms"),
        ip_hash=ip_hash,
        user_agent=user_agent,
        occurred_at=occurred_at,
    )

    destination = data.get("url") or _fallback_url()
    logger.info("sms_click_redirect", token=token, destination=destination)
    return RedirectResponse(url=destination, status_code=302)


@router.get("/px/{token}.gif", include_in_schema=False)
async def open_pixel(token: str, request: Request):
    """Return a 1×1 transparent GIF and queue an open TrackerEvent."""
    from src.tasks.tracking_tasks import log_tracker_event

    data = get_tracking_data(token)

    if data is not None:
        ip_hash = _hash_ip(request.client.host if request.client else None)
        user_agent = request.headers.get("user-agent")
        occurred_at = datetime.now(timezone.utc).isoformat()

        log_tracker_event.delay(
            event_type="open",
            token=token,
            lead_id=data.get("lead_id"),
            campaign_id=data.get("campaign_id"),
            target_id=data.get("target_id"),
            channel=data.get("channel", "email"),
            ip_hash=ip_hash,
            user_agent=user_agent,
            occurred_at=occurred_at,
        )
        logger.info("open_pixel_fired", token=token)
    else:
        logger.info("open_pixel_token_not_found", token=token)

    return Response(
        content=_TRANSPARENT_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
