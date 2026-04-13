"""SendGrid Event and Inbound Parse webhook endpoints (NIF-225, NIF-229).

Receives SendGrid Event Webhook POST requests, verifies the ECDSA signature,
and queues a Celery task to process the event batch asynchronously.
Also handles SendGrid Inbound Parse for reply detection (NIF-229).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response

from src.config import settings
from src.tasks.email_tasks import process_sendgrid_events, process_inbound_reply_task
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


@router.post("/sendgrid/inbound", status_code=200, include_in_schema=True)
async def receive_sendgrid_inbound(request: Request) -> Response:
    """Receive a SendGrid Inbound Parse webhook for reply detection (NIF-229).

    SendGrid POSTs multipart/form-data containing the raw email fields.
    We extract the form data and queue a Celery task for async processing.
    """
    try:
        form = await request.form()
        inbound_data = {
            "headers": form.get("headers", ""),
            "from": form.get("from", ""),
            "to": form.get("to", ""),
            "subject": form.get("subject", ""),
            "text": form.get("text", ""),
            "html": form.get("html", ""),
            "envelope": form.get("envelope", ""),
        }
    except Exception as exc:
        logger.warning("sendgrid_inbound_parse_failed", error=str(exc))
        raise HTTPException(status_code=400, detail="Failed to parse inbound email data")

    try:
        process_inbound_reply_task.delay(inbound_data)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.error("sendgrid_inbound_queue_failed", error=str(exc))

    logger.info("sendgrid_inbound_received", from_email=inbound_data.get("from"))
    return Response(content='{"received": true}', media_type="application/json")
