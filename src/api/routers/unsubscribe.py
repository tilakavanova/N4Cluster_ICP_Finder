"""Unsubscribe management endpoints (NIF-227).

Provides token-based unsubscribe pages and a one-click unsubscribe
endpoint for RFC 8058 List-Unsubscribe-Post header support.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

import redis
from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from src.config import settings
from src.db.session import async_session
from src.db.models import Lead, TrackerEvent
from src.utils.logging import get_logger

logger = get_logger("unsubscribe")

router = APIRouter(prefix="/unsubscribe", tags=["unsubscribe"])

# Token TTL: 90 days
_TOKEN_TTL = 7_776_000

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def generate_unsubscribe_token(
    lead_id: str,
    target_id: str | None = None,
    campaign_id: str | None = None,
    *,
    redis_client: redis.Redis | None = None,
) -> str:
    """Generate and store an unsubscribe token for the given lead.

    Returns a URL-safe token string.
    """
    token = secrets.token_urlsafe(16)
    data = {
        "lead_id": lead_id,
        "target_id": target_id,
        "campaign_id": campaign_id,
    }
    client = redis_client or _get_redis()
    client.setex(f"unsub:{token}", _TOKEN_TTL, json.dumps(data))
    return token


def get_unsubscribe_data(
    token: str,
    *,
    redis_client: redis.Redis | None = None,
) -> dict | None:
    """Return stored token data, or None if expired/not found."""
    client = redis_client or _get_redis()
    raw = client.get(f"unsub:{token}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _confirmation_html(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Unsubscribe</title>
<style>body{{font-family:sans-serif;max-width:480px;margin:80px auto;padding:0 20px;color:#333}}
h1{{font-size:1.4rem}}p{{line-height:1.6}}</style></head>
<body><h1>N4Cluster — Email Preferences</h1><p>{message}</p></body>
</html>"""


def _confirmation_form_html(token: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Unsubscribe</title>
<style>body{{font-family:sans-serif;max-width:480px;margin:80px auto;padding:0 20px;color:#333}}
h1{{font-size:1.4rem}}p{{line-height:1.6}}
button{{background:#d9534f;color:#fff;border:none;padding:10px 20px;cursor:pointer;border-radius:4px;font-size:1rem}}</style>
</head>
<body>
<h1>N4Cluster — Unsubscribe</h1>
<p>Click the button below to unsubscribe from our restaurant outreach emails.</p>
<form method="post" action="/unsubscribe/{token}">
  <button type="submit">Unsubscribe me</button>
</form>
<p style="font-size:0.85rem;color:#999">You can re-subscribe at any time by contacting us.</p>
</body>
</html>"""


@router.post("/one-click", include_in_schema=True)
async def one_click_unsubscribe(
    request: Request,
    List_Unsubscribe: str | None = Form(default=None, alias="List-Unsubscribe"),
) -> Response:
    """RFC 8058 one-click unsubscribe endpoint.

    Called by mail clients that support the ``List-Unsubscribe-Post`` header.
    Expects ``List-Unsubscribe=One-Click`` in the form body, along with an
    ``email`` parameter identifying the recipient.
    """
    import uuid as _uuid

    form = await request.form()
    email = form.get("email") or form.get("recipient")

    if not email:
        raise HTTPException(status_code=422, detail="Missing 'email' in form body")

    async with async_session() as session:
        from sqlalchemy import select  # noqa: PLC0415
        result = await session.execute(
            select(Lead).where(Lead.email == email)
        )
        lead = result.scalar_one_or_none()
        if lead:
            lead.email_opt_out = True
            tracker = TrackerEvent(
                token=None,
                event_type="unsubscribe",
                channel="email",
                lead_id=lead.id,
                provider="n4cluster",
                provider_event_id=f"one-click-unsub:{email}",
                occurred_at=datetime.now(timezone.utc),
            )
            session.add(tracker)
            await session.commit()
            logger.info("one_click_unsubscribe_processed", email=email, lead_id=str(lead.id))
        else:
            logger.warning("one_click_unsubscribe_lead_not_found", email=email)

    return Response(content='{"unsubscribed": true}', media_type="application/json")


@router.get("/{token}", response_class=HTMLResponse, include_in_schema=True)
async def unsubscribe_confirm_page(token: str) -> HTMLResponse:
    """Render an unsubscribe confirmation page for the given token."""
    data = get_unsubscribe_data(token)
    if data is None:
        return HTMLResponse(
            content=_confirmation_html(
                "This unsubscribe link has expired or is invalid. "
                "Please contact us directly if you wish to be removed."
            ),
            status_code=404,
        )
    return HTMLResponse(content=_confirmation_form_html(token), status_code=200)


@router.post("/{token}", response_class=HTMLResponse, include_in_schema=True)
async def process_unsubscribe(token: str) -> HTMLResponse:
    """Process the unsubscribe: set email_opt_out on Lead and log a TrackerEvent."""
    import uuid as _uuid

    data = get_unsubscribe_data(token)
    if data is None:
        raise HTTPException(status_code=404, detail="Invalid or expired unsubscribe token")

    lead_id_str = data.get("lead_id")
    target_id_str = data.get("target_id")
    campaign_id_str = data.get("campaign_id")

    async with async_session() as session:
        # Opt out the lead
        if lead_id_str:
            lead = await session.get(Lead, _uuid.UUID(lead_id_str))
            if lead:
                lead.email_opt_out = True

        # Record the unsubscribe event
        tracker = TrackerEvent(
            token=token,
            event_type="unsubscribe",
            channel="email",
            lead_id=_uuid.UUID(lead_id_str) if lead_id_str else None,
            campaign_id=_uuid.UUID(campaign_id_str) if campaign_id_str else None,
            target_id=_uuid.UUID(target_id_str) if target_id_str else None,
            provider="n4cluster",
            provider_event_id=f"unsub:{token}",
            occurred_at=datetime.now(timezone.utc),
        )
        session.add(tracker)
        await session.commit()

    logger.info("unsubscribe_processed", lead_id=lead_id_str, token=token)

    return HTMLResponse(
        content=_confirmation_html(
            "You have been successfully unsubscribed. "
            "You will no longer receive outreach emails from us."
        ),
        status_code=200,
    )
