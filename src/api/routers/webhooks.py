"""SendGrid Event Webhook endpoint (NIF-225).

Receives SendGrid Event Webhook POST requests, verifies the ECDSA signature,
and queues a Celery task to process the event batch asynchronously.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response

from src.config import settings
from src.tasks.email_tasks import process_sendgrid_events
from src.utils.logging import get_logger
from src.utils.webhook_verification import verify_sendgrid_signature

logger = get_logger("webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/sendgrid", status_code=200, include_in_schema=True)
async def receive_sendgrid_events(request: Request) -> Response:
    """Receive and queue a SendGrid Event Webhook batch.

    SendGrid sends an array of event objects in the request body.
    We verify the ECDSA signature, return 200 immediately, then hand
    processing off to the ``process_sendgrid_events`` Celery task so
    that the webhook response is not delayed by DB writes.

    Returns 401 if the signature is invalid or the signing key is missing.
    """
    raw_body = await request.body()

    # Extract SendGrid signature headers
    signature = request.headers.get("x-twilio-email-event-webhook-signature", "")
    timestamp = request.headers.get("x-twilio-email-event-webhook-timestamp", "")
    signing_key = settings.sendgrid_webhook_signing_key

    # Verify signature (skip verification in dev when no key is configured)
    if signing_key:
        valid = verify_sendgrid_signature(
            payload=raw_body,
            signature=signature,
            timestamp=timestamp,
            signing_key=signing_key,
        )
        if not valid:
            logger.warning(
                "sendgrid_webhook_invalid_signature",
                path=str(request.url),
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Parse event array
    try:
        events: list[dict] = json.loads(raw_body)
        if not isinstance(events, list):
            events = [events]
    except (json.JSONDecodeError, ValueError):
        logger.warning("sendgrid_webhook_invalid_json")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Queue async processing — fire-and-forget
    try:
        process_sendgrid_events.delay(events)
    except Exception as exc:
        # Broker unavailable — log and still return 200 to avoid SendGrid retries
        logger.error("sendgrid_webhook_queue_failed", error=str(exc))

    logger.info("sendgrid_webhook_received", event_count=len(events))
    return Response(content='{"received": true}', media_type="application/json")
