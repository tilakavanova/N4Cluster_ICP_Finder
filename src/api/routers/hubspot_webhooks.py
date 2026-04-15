"""HubSpot bidirectional webhook endpoint (NIF-257).

Receives HubSpot CRM webhook events, verifies the v3 HMAC-SHA256 signature,
and queues a Celery task to process deal/contact property changes and sync
them back to the local Lead model.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response

from src.config import settings
from src.tasks.hubspot_tasks import process_hubspot_webhook
from src.utils.logging import get_logger
from src.utils.webhook_verification import verify_hubspot_signature

logger = get_logger("webhooks.hubspot")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/hubspot", status_code=200, include_in_schema=True)
async def receive_hubspot_events(request: Request) -> Response:
    """Receive and queue HubSpot CRM webhook events.

    HubSpot sends batched event objects (deal.propertyChange,
    contact.propertyChange, etc.).  We verify the HMAC-SHA256 v3 signature,
    return 200 immediately, then hand processing off to the
    ``process_hubspot_webhook`` Celery task so the webhook is not delayed
    by DB writes.

    Returns 401 if the signature is invalid.
    Returns 400 if the request body is not valid JSON.
    """
    raw_body = await request.body()

    client_secret = settings.hubspot_webhook_secret

    if client_secret:
        # Build the v3 source string: {HTTP_METHOD}{full_url}{request_body}
        full_url = str(request.url)
        source_string = f"POST{full_url}{raw_body.decode('utf-8', errors='replace')}"

        signature = request.headers.get("x-hubspot-signature-v3", "")
        timestamp = request.headers.get("x-hubspot-request-timestamp", "")

        valid = verify_hubspot_signature(
            request_body=raw_body,
            signature=signature,
            client_secret=client_secret,
            source_string=source_string,
            timestamp=timestamp,
        )
        if not valid:
            logger.warning(
                "hubspot_webhook_invalid_signature",
                path=str(request.url),
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Parse event array — HubSpot sends a JSON array of event objects
    try:
        events: list[dict] = json.loads(raw_body)
        if not isinstance(events, list):
            events = [events]
    except (json.JSONDecodeError, ValueError):
        logger.warning("hubspot_webhook_invalid_json")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Queue async processing — fire-and-forget
    try:
        process_hubspot_webhook.delay(events)
    except Exception as exc:
        # Broker unavailable — log and still return 200 to avoid HubSpot retries
        logger.error("hubspot_webhook_queue_failed", error=str(exc))

    logger.info("hubspot_webhook_received", event_count=len(events))
    return Response(content='{"received": true}', media_type="application/json")
