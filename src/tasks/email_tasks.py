"""Celery tasks for email sending and SendGrid event processing (NIF-226, NIF-225, NIF-228)."""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone

from src.tasks.celery_app import celery_app
from src.tasks.crawl_tasks import run_async
from src.utils.logging import get_logger
from src.services.reply_detection import detect_reply, process_inbound_reply

logger = get_logger("tasks.email")

# Map SendGrid event types to our status handler names
_SG_EVENT_HANDLER: dict[str, str] = {
    "delivered": "mark_as_delivered",
    "open": "mark_as_opened",
    "click": "mark_as_clicked",
    "bounce": "mark_as_bounced",
    "dropped": "mark_as_failed",
    "spamreport": "mark_as_opted_out",
    "unsubscribe": "mark_as_opted_out",
}

# Apple MPP fingerprint: Mozilla/5.0 without a real browser token
_APPLE_MPP_UA_MARKER = "Mozilla/5.0"
_APPLE_MPP_REAL_BROWSER_MARKERS = (
    "Chrome/", "Firefox/", "Safari/", "Edge/", "Edg/",
)


def _is_apple_mpp(user_agent: str | None) -> bool:
    """Return True when the User-Agent looks like Apple Mail Privacy Protection.

    Apple MPP pre-fetches pixels using a plain "Mozilla/5.0" UA without any
    real browser version tokens such as "Chrome/", "Firefox/", or "Safari/".
    """
    if not user_agent:
        return False
    if _APPLE_MPP_UA_MARKER not in user_agent:
        return False
    return not any(marker in user_agent for marker in _APPLE_MPP_REAL_BROWSER_MARKERS)


# ── send_email_task ──────────────────────────────────────────────────────────


@celery_app.task(
    name="src.tasks.email_tasks.send_email_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_email_task(
    self,
    target_id: str,
    lead_id: str,
    campaign_id: str,
    subject: str,
    html_content: str,
    text_content: str | None = None,
):
    """Celery wrapper for send_outreach_email — dispatches asynchronously.

    Args:
        target_id: UUID string of the OutreachTarget.
        lead_id: UUID string of the Lead.
        campaign_id: UUID string of the OutreachCampaign.
        subject: Email subject line.
        html_content: HTML body (URLs will be wrapped, pixel injected).
        text_content: Optional plain-text alternative.
    """
    async def _send():
        from src.db.session import async_session
        from src.services.email_service import send_outreach_email

        async with async_session() as session:
            return await send_outreach_email(
                session=session,
                target_id=_uuid.UUID(target_id),
                lead_id=_uuid.UUID(lead_id),
                campaign_id=_uuid.UUID(campaign_id),
                subject=subject,
                html_content=html_content,
                text_content=text_content,
            )

    try:
        result = run_async(_send())
        logger.info(
            "send_email_task_complete",
            target_id=target_id,
            status=result.get("status"),
            message_id=result.get("message_id"),
        )
        return result
    except Exception as exc:
        logger.error("send_email_task_failed", target_id=target_id, error=str(exc))
        raise self.retry(exc=exc)


# ── process_sendgrid_events ──────────────────────────────────────────────────


@celery_app.task(
    name="src.tasks.email_tasks.process_sendgrid_events",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def process_sendgrid_events(self, events: list[dict]):
    """Process a batch of SendGrid Event Webhook events.

    For each event:
    - Matches the OutreachActivity via sg_message_id → external_message_id
    - Calls the appropriate communication_status helper
    - Creates a TrackerEvent via the state machine
    - Handles deduplication via provider_event_id (sg_event_id)
    - Detects Apple MPP proxy opens and flags them in metadata

    Args:
        events: List of SendGrid event dicts from the webhook payload.
    """
    async def _process():
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from src.db.session import async_session
        from src.db.models import Lead, OutreachActivity, OutreachTarget, TrackerEvent
        import src.services.communication_status as cs

        processed = 0
        skipped = 0
        errors = 0

        async with async_session() as session:
            for event in events:
                sg_event_type: str = event.get("event", "")
                sg_event_id: str = event.get("sg_event_id") or event.get("sg_message_id", "")
                sg_message_id: str = event.get("sg_message_id", "")
                user_agent: str | None = event.get("useragent") or event.get("user_agent")
                occurred_ts_raw = event.get("timestamp")
                occurred_at = (
                    datetime.fromtimestamp(int(occurred_ts_raw), tz=timezone.utc)
                    if occurred_ts_raw
                    else datetime.now(timezone.utc)
                )

                # Build a stable dedup key
                provider_event_id = f"sg:{sg_event_id}" if sg_event_id else f"sg:{sg_message_id}:{sg_event_type}:{occurred_ts_raw}"

                # Deduplication check
                existing = await session.execute(
                    select(TrackerEvent).where(
                        TrackerEvent.provider_event_id == provider_event_id
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    logger.info("sendgrid_event_duplicate_skipped", provider_event_id=provider_event_id)
                    skipped += 1
                    continue

                # Resolve OutreachActivity → OutreachTarget via sg_message_id
                target_id: _uuid.UUID | None = None
                lead_id: _uuid.UUID | None = None
                campaign_id: _uuid.UUID | None = None

                if sg_message_id:
                    activity_result = await session.execute(
                        select(OutreachActivity).where(
                            OutreachActivity.external_message_id == sg_message_id
                        )
                    )
                    activity = activity_result.scalar_one_or_none()
                    if activity:
                        target_id = activity.target_id
                        # Load target to get lead_id and campaign_id
                        target = await session.get(OutreachTarget, target_id)
                        if target:
                            lead_id = target.lead_id
                            campaign_id = target.campaign_id

                # Build event metadata
                metadata: dict = {}
                if user_agent:
                    metadata["user_agent"] = user_agent
                if sg_event_id:
                    metadata["sg_event_id"] = sg_event_id

                # Detect Apple MPP proxy open
                is_proxy_open = False
                if sg_event_type == "open" and _is_apple_mpp(user_agent):
                    is_proxy_open = True
                    metadata["apple_mpp"] = True
                    logger.info("apple_mpp_detected", sg_message_id=sg_message_id)

                # NIF-228: classify bounce type (hard vs soft)
                # SendGrid bounce events carry a `type` field: "bounce" = hard, "blocked" = soft
                bounce_classification: str = "hard"
                is_soft_bounce = False
                if sg_event_type == "bounce":
                    sg_bounce_type = event.get("type", "bounce")
                    # "blocked" or type containing "soft" → soft bounce
                    if sg_bounce_type in ("blocked", "soft") or "soft" in sg_bounce_type.lower():
                        is_soft_bounce = True
                        bounce_classification = "soft"
                        metadata["bounce_classification"] = "soft"
                    else:
                        metadata["bounce_classification"] = "hard"
                        metadata["bounce_type"] = sg_bounce_type

                # Apply status transition if we have a target
                if target_id is not None:
                    handler_name = _SG_EVENT_HANDLER.get(sg_event_type)
                    if handler_name:
                        handler = getattr(cs, handler_name, None)
                        if handler:
                            try:
                                if sg_event_type == "bounce":
                                    if not is_soft_bounce:
                                        # Hard bounce: transition to BOUNCED
                                        await handler(session, target_id, "email", bounce_type="hard")
                                    else:
                                        # Soft bounce: log only, no status transition
                                        logger.info(
                                            "sendgrid_soft_bounce_skipped",
                                            target_id=str(target_id),
                                            sg_bounce_type=event.get("type"),
                                        )
                                elif sg_event_type == "dropped":
                                    await handler(session, target_id, "email", error_reason="dropped")
                                else:
                                    await handler(session, target_id, "email")
                            except Exception as exc:
                                logger.warning(
                                    "sendgrid_status_transition_failed",
                                    target_id=str(target_id),
                                    event_type=sg_event_type,
                                    error=str(exc),
                                )

                    # NIF-228: Hard bounce → opt out Lead
                    if sg_event_type == "bounce" and not is_soft_bounce and lead_id:
                        lead = await session.get(Lead, lead_id)
                        if lead:
                            lead.email_opt_out = True
                            logger.info("lead_hard_bounce_opt_out", lead_id=str(lead_id))

                    # NIF-228: Spam/complaint → opt out Lead
                    if sg_event_type in ("spamreport", "unsubscribe") and lead_id:
                        lead = await session.get(Lead, lead_id)
                        if lead:
                            lead.email_opt_out = True

                # Persist TrackerEvent
                tracker_event = TrackerEvent(
                    token=None,
                    event_type=_sg_event_type_to_tracker(sg_event_type),
                    channel="email",
                    lead_id=lead_id,
                    campaign_id=campaign_id,
                    target_id=target_id,
                    provider="sendgrid",
                    provider_event_id=provider_event_id,
                    event_metadata=metadata or None,
                    occurred_at=occurred_at,
                )
                session.add(tracker_event)

                try:
                    await session.flush()
                    processed += 1
                except IntegrityError:
                    await session.rollback()
                    logger.info(
                        "sendgrid_event_integrity_skipped",
                        provider_event_id=provider_event_id,
                    )
                    skipped += 1
                    continue

            await session.commit()

        logger.info(
            "sendgrid_events_processed",
            total=len(events),
            processed=processed,
            skipped=skipped,
            errors=errors,
        )
        return {"processed": processed, "skipped": skipped, "errors": errors}

    try:
        return run_async(_process())
    except Exception as exc:
        logger.error("process_sendgrid_events_failed", error=str(exc))
        raise self.retry(exc=exc)


# ── process_inbound_reply_task ───────────────────────────────────────────────


@celery_app.task(
    name="src.tasks.email_tasks.process_inbound_reply_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def process_inbound_reply_task(self, inbound_data: dict):
    """Process a SendGrid Inbound Parse payload to detect and record email replies (NIF-229).

    Args:
        inbound_data: Dict of inbound email fields from the SendGrid Inbound Parse webhook.
    """
    async def _process():
        from src.db.session import async_session

        reply_data = detect_reply(inbound_data)

        if not reply_data["is_likely_reply"]:
            logger.info(
                "inbound_email_not_reply",
                from_email=reply_data.get("from_email"),
                subject=reply_data.get("subject"),
            )
            return {"matched": False, "reason": "not_a_reply"}

        async with async_session() as session:
            result = await process_inbound_reply(session, reply_data)
            await session.commit()

        return result

    try:
        return run_async(_process())
    except Exception as exc:
        logger.error("process_inbound_reply_task_failed", error=str(exc))
        raise self.retry(exc=exc)


def _sg_event_type_to_tracker(sg_event_type: str) -> str:
    """Map a SendGrid event type string to our TrackerEvent.event_type."""
    return {
        "delivered": "delivery",
        "open": "open",
        "click": "click",
        "bounce": "bounce",
        "dropped": "bounce",
        "spamreport": "unsubscribe",
        "unsubscribe": "unsubscribe",
        "deferred": "delivery",
        "processed": "delivery",
    }.get(sg_event_type, sg_event_type)
