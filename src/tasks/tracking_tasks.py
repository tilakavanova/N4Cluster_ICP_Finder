"""Celery tasks for click/open tracking event persistence (NIF-223)."""

import hashlib
from datetime import datetime, timezone

from src.tasks.celery_app import celery_app
from src.tasks.crawl_tasks import run_async
from src.utils.logging import get_logger

logger = get_logger("tasks.tracking")


@celery_app.task(
    name="src.tasks.tracking_tasks.log_tracker_event",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def log_tracker_event(
    self,
    event_type: str,
    token: str,
    lead_id: str | None,
    campaign_id: str | None,
    target_id: str | None,
    channel: str,
    ip_hash: str | None = None,
    user_agent: str | None = None,
    occurred_at: str | None = None,
):
    """Persist a TrackerEvent row for a click or open.

    Uses ``provider_event_id = f"{token}:{event_type}"`` for deduplication so
    that retried tasks or double-fires don't create duplicate rows.

    Args:
        event_type: "click" or "open".
        token: The tracking token that was looked up.
        lead_id: UUID string of the lead (may be None).
        campaign_id: UUID string of the campaign (may be None).
        target_id: UUID string of the outreach target (may be None).
        channel: Delivery channel, e.g. "email".
        ip_hash: SHA-256 hex of the visitor's IP (already hashed by the router).
        user_agent: Raw User-Agent header value.
        occurred_at: ISO-8601 timestamp string; defaults to now if omitted.
    """
    async def _persist():
        import uuid as _uuid
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from src.db.session import async_session
        from src.db.models import TrackerEvent

        provider_event_id = f"{token}:{event_type}"

        ts = (
            datetime.fromisoformat(occurred_at)
            if occurred_at
            else datetime.now(timezone.utc)
        )

        metadata: dict = {}
        if ip_hash:
            metadata["ip_hash"] = ip_hash
        if user_agent:
            metadata["user_agent"] = user_agent

        async with async_session() as session:
            # Deduplication check — provider_event_id has a UNIQUE constraint
            existing = await session.execute(
                select(TrackerEvent).where(
                    TrackerEvent.provider_event_id == provider_event_id
                )
            )
            if existing.scalar_one_or_none() is not None:
                logger.info(
                    "tracker_event_duplicate_skipped",
                    provider_event_id=provider_event_id,
                )
                return {"status": "duplicate"}

            event = TrackerEvent(
                token=token,
                event_type=event_type,
                channel=channel,
                lead_id=_uuid.UUID(lead_id) if lead_id else None,
                campaign_id=_uuid.UUID(campaign_id) if campaign_id else None,
                target_id=_uuid.UUID(target_id) if target_id else None,
                provider="self",
                provider_event_id=provider_event_id,
                event_metadata=metadata or None,
                occurred_at=ts,
            )
            session.add(event)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.info(
                    "tracker_event_integrity_error_skipped",
                    provider_event_id=provider_event_id,
                )
                return {"status": "duplicate"}

        logger.info(
            "tracker_event_persisted",
            event_type=event_type,
            token=token,
            provider_event_id=provider_event_id,
        )
        return {"status": "ok"}

    try:
        return run_async(_persist())
    except Exception as exc:
        logger.error("tracker_event_task_failed", error=str(exc))
        raise self.retry(exc=exc)
