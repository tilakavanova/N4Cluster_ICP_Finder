"""Celery tasks for HubSpot bidirectional webhook processing (NIF-257).

Processes inbound HubSpot CRM events (deal.propertyChange,
contact.propertyChange) and syncs changes back to the local Lead model,
creating LeadStageHistory records and AuditLog entries as appropriate.
"""

from __future__ import annotations

from src.tasks.celery_app import celery_app
from src.tasks.crawl_tasks import run_async
from src.utils.logging import get_logger

logger = get_logger("tasks.hubspot")

# ---------------------------------------------------------------------------
# Stage mapping: HubSpot deal stage → local lifecycle_stage
# ---------------------------------------------------------------------------

HUBSPOT_STAGE_MAP: dict[str, str] = {
    "qualifiedtobuy": "qualified",
    "presentationscheduled": "demo_scheduled",
    "closedwon": "converted",
    "closedlost": "lost",
}

# Map HubSpot contact properties to Lead model fields
CONTACT_FIELD_MAP: dict[str, str] = {
    "firstname": "first_name",
    "lastname": "last_name",
    "company": "company",
}


# ---------------------------------------------------------------------------
# process_hubspot_webhook
# ---------------------------------------------------------------------------


@celery_app.task(
    name="src.tasks.hubspot_tasks.process_hubspot_webhook",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def process_hubspot_webhook(self, events: list[dict]):
    """Process a batch of HubSpot webhook events.

    For each event:
    - Matches the local Lead via hubspot_contact_id or email lookup.
    - On ``deal.propertyChange`` with ``dealstage``: maps HubSpot stage to
      local lifecycle_stage, updates Lead, and creates a LeadStageHistory.
    - On ``deal.propertyChange`` with ``closedate``: updates Lead metadata.
    - On ``contact.propertyChange``: updates matching Lead fields.
    - Logs every sync action to AuditLog.

    Args:
        events: List of HubSpot event dicts from the webhook payload.
    """
    async def _process():
        from datetime import datetime, timezone

        from sqlalchemy import select

        from src.db.session import async_session
        from src.db.models import AuditLog, Lead, LeadStageHistory

        processed = 0
        skipped = 0

        async with async_session() as session:
            for event in events:
                subscription_type: str = event.get("subscriptionType", "")
                object_id: str = str(event.get("objectId", ""))
                property_name: str = event.get("propertyName", "")
                property_value: str = event.get("propertyValue", "") or ""

                try:
                    # ── deal.propertyChange ──────────────────────────────
                    if subscription_type == "deal.propertyChange":
                        lead = await _find_lead_by_deal_id(session, object_id, Lead)

                        if lead is None:
                            logger.warning(
                                "hubspot_webhook_no_lead_for_deal",
                                deal_id=object_id,
                                property_name=property_name,
                            )
                            skipped += 1
                            continue

                        if property_name == "dealstage":
                            await _handle_deal_stage_change(
                                session, lead, property_value, LeadStageHistory, AuditLog
                            )
                            processed += 1

                        elif property_name == "closedate":
                            await _handle_close_date_change(
                                session, lead, property_value, AuditLog
                            )
                            processed += 1

                        else:
                            logger.debug(
                                "hubspot_webhook_deal_property_ignored",
                                property_name=property_name,
                                deal_id=object_id,
                            )
                            skipped += 1

                    # ── contact.propertyChange ───────────────────────────
                    elif subscription_type == "contact.propertyChange":
                        lead = await _find_lead_by_contact_id(session, object_id, Lead)

                        if lead is None:
                            logger.warning(
                                "hubspot_webhook_no_lead_for_contact",
                                contact_id=object_id,
                                property_name=property_name,
                            )
                            skipped += 1
                            continue

                        await _handle_contact_property_change(
                            session, lead, property_name, property_value, AuditLog
                        )
                        processed += 1

                    else:
                        logger.debug(
                            "hubspot_webhook_event_ignored",
                            subscription_type=subscription_type,
                        )
                        skipped += 1

                except Exception as exc:
                    logger.error(
                        "hubspot_webhook_event_error",
                        subscription_type=subscription_type,
                        object_id=object_id,
                        error=str(exc),
                    )
                    skipped += 1

            await session.commit()

        logger.info(
            "hubspot_webhook_processed",
            total=len(events),
            processed=processed,
            skipped=skipped,
        )
        return {"processed": processed, "skipped": skipped}

    try:
        return run_async(_process())
    except Exception as exc:
        logger.error("process_hubspot_webhook_failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _find_lead_by_deal_id(session, deal_id: str, Lead):
    """Find a Lead whose hubspot_deal_id matches the given HubSpot deal ID."""
    from sqlalchemy import select

    result = await session.execute(
        select(Lead).where(Lead.hubspot_deal_id == deal_id)
    )
    return result.scalar_one_or_none()


async def _find_lead_by_contact_id(session, contact_id: str, Lead):
    """Find a Lead whose hubspot_contact_id matches the given HubSpot contact ID."""
    from sqlalchemy import select

    result = await session.execute(
        select(Lead).where(Lead.hubspot_contact_id == contact_id)
    )
    return result.scalar_one_or_none()


async def _find_lead_by_email(session, email: str, Lead):
    """Find a Lead by email address (case-insensitive fallback lookup)."""
    from sqlalchemy import func, select

    result = await session.execute(
        select(Lead).where(func.lower(Lead.email) == email.lower())
    )
    return result.scalar_one_or_none()


async def _handle_deal_stage_change(session, lead, hs_stage: str, LeadStageHistory, AuditLog):
    """Map HubSpot deal stage to local lifecycle_stage and persist the change."""
    from datetime import datetime, timezone

    local_stage = HUBSPOT_STAGE_MAP.get(hs_stage)

    if local_stage is None:
        logger.warning(
            "hubspot_webhook_unknown_deal_stage",
            hs_stage=hs_stage,
            lead_id=str(lead.id),
        )
        # Log but do not crash — unknown stages are silently skipped
        session.add(AuditLog(
            action="hubspot_stage_unknown",
            entity_type="lead",
            details={
                "lead_id": str(lead.id),
                "hubspot_stage": hs_stage,
                "hubspot_deal_id": lead.hubspot_deal_id,
            },
            performed_by="hubspot_webhook",
        ))
        return

    old_stage = lead.lifecycle_stage

    if old_stage == local_stage:
        logger.debug(
            "hubspot_webhook_stage_unchanged",
            lead_id=str(lead.id),
            stage=local_stage,
        )
        return

    # Update lead stage
    lead.lifecycle_stage = local_stage
    lead.updated_at = datetime.now(timezone.utc)

    # Create history record
    history = LeadStageHistory(
        lead_id=lead.id,
        from_stage=old_stage,
        to_stage=local_stage,
        changed_by="hubspot_webhook",
        changed_at=datetime.now(timezone.utc),
    )
    session.add(history)

    # Audit log entry
    session.add(AuditLog(
        action="hubspot_stage_sync",
        entity_type="lead",
        details={
            "lead_id": str(lead.id),
            "from_stage": old_stage,
            "to_stage": local_stage,
            "hubspot_stage": hs_stage,
            "hubspot_deal_id": lead.hubspot_deal_id,
        },
        performed_by="hubspot_webhook",
    ))

    logger.info(
        "hubspot_webhook_stage_updated",
        lead_id=str(lead.id),
        from_stage=old_stage,
        to_stage=local_stage,
        hs_stage=hs_stage,
    )


async def _handle_close_date_change(session, lead, close_date_value: str, AuditLog):
    """Record a deal close-date change in the audit log and update Lead metadata."""
    from datetime import datetime, timezone

    lead.updated_at = datetime.now(timezone.utc)

    session.add(AuditLog(
        action="hubspot_closedate_sync",
        entity_type="lead",
        details={
            "lead_id": str(lead.id),
            "close_date": close_date_value,
            "hubspot_deal_id": lead.hubspot_deal_id,
        },
        performed_by="hubspot_webhook",
    ))

    logger.info(
        "hubspot_webhook_closedate_updated",
        lead_id=str(lead.id),
        close_date=close_date_value,
    )


async def _handle_contact_property_change(session, lead, property_name: str, property_value: str, AuditLog):
    """Apply a HubSpot contact property change to the local Lead."""
    from datetime import datetime, timezone

    local_field = CONTACT_FIELD_MAP.get(property_name)

    if local_field is None:
        logger.debug(
            "hubspot_webhook_contact_property_ignored",
            property_name=property_name,
            lead_id=str(lead.id),
        )
        return

    old_value = getattr(lead, local_field, None)
    setattr(lead, local_field, property_value)
    lead.updated_at = datetime.now(timezone.utc)

    session.add(AuditLog(
        action="hubspot_contact_sync",
        entity_type="lead",
        details={
            "lead_id": str(lead.id),
            "field": local_field,
            "old_value": old_value,
            "new_value": property_value,
            "hubspot_contact_id": lead.hubspot_contact_id,
        },
        performed_by="hubspot_webhook",
    ))

    logger.info(
        "hubspot_webhook_contact_updated",
        lead_id=str(lead.id),
        field=local_field,
        new_value=property_value,
    )
