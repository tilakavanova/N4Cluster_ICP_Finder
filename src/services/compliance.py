"""GDPR compliance and data retention service (NIF-241).

Provides:
- Data export (right of access)
- Data erasure (right to erasure) with HubSpot removal
- Consent management with timestamp and scope
- Scheduled data retention cleanup
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Lead, AuditLog,
    OutreachTarget, OutreachActivity, TrackerEvent,
    ConversionEvent, LeadStageHistory, LeadAssignmentHistory,
    FollowUpTask,
)
from src.services.hubspot import HubSpotService
from src.utils.logging import get_logger

logger = get_logger("compliance")

# Default data retention days — overridable via config
DEFAULT_DATA_RETENTION_DAYS = 730  # 2 years

_REDACTED = "[REDACTED]"


async def export_lead_data(session: AsyncSession, lead_id: UUID) -> dict:
    """Export all PII and related data for a lead (right of access).

    Returns a JSON-serializable dict with all personal data.
    """
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        return {}

    # Stage history
    stage_result = await session.execute(
        select(LeadStageHistory).where(LeadStageHistory.lead_id == lead_id)
        .order_by(LeadStageHistory.changed_at.asc())
    )
    stages = [
        {
            "from_stage": s.from_stage,
            "to_stage": s.to_stage,
            "changed_by": s.changed_by,
            "changed_at": s.changed_at.isoformat() if s.changed_at else None,
        }
        for s in stage_result.scalars().all()
    ]

    # Assignment history
    assign_result = await session.execute(
        select(LeadAssignmentHistory).where(LeadAssignmentHistory.lead_id == lead_id)
        .order_by(LeadAssignmentHistory.changed_at.asc())
    )
    assignments = [
        {
            "from_owner": a.from_owner,
            "to_owner": a.to_owner,
            "changed_by": a.changed_by,
            "changed_at": a.changed_at.isoformat() if a.changed_at else None,
        }
        for a in assign_result.scalars().all()
    ]

    # Conversion events
    conv_result = await session.execute(
        select(ConversionEvent).where(ConversionEvent.lead_id == lead_id)
        .order_by(ConversionEvent.occurred_at.asc())
    )
    conversions = [
        {
            "event_type": c.event_type,
            "source": c.source,
            "occurred_at": c.occurred_at.isoformat() if c.occurred_at else None,
        }
        for c in conv_result.scalars().all()
    ]

    # Follow-up tasks
    task_result = await session.execute(
        select(FollowUpTask).where(FollowUpTask.lead_id == lead_id)
    )
    tasks = [
        {
            "title": t.title,
            "task_type": t.task_type,
            "status": t.status,
            "due_date": t.due_date.isoformat() if t.due_date else None,
        }
        for t in task_result.scalars().all()
    ]

    # Tracker events
    tracker_result = await session.execute(
        select(TrackerEvent).where(TrackerEvent.lead_id == lead_id)
    )
    tracker_events = [
        {
            "event_type": te.event_type,
            "channel": te.channel,
            "occurred_at": te.occurred_at.isoformat() if te.occurred_at else None,
        }
        for te in tracker_result.scalars().all()
    ]

    export = {
        "lead_id": str(lead.id),
        "personal_data": {
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "email": lead.email,
            "company": lead.company,
            "business_type": lead.business_type,
            "locations": lead.locations,
            "interest": lead.interest,
            "message": lead.message,
        },
        "metadata": {
            "source": lead.source,
            "status": lead.status,
            "lifecycle_stage": lead.lifecycle_stage,
            "owner": lead.owner,
            "icp_fit_label": lead.icp_fit_label,
            "icp_total_score": lead.icp_total_score,
            "created_at": lead.created_at.isoformat() if lead.created_at else None,
            "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
        },
        "tracking": {
            "utm_source": lead.utm_source,
            "utm_medium": lead.utm_medium,
            "utm_campaign": lead.utm_campaign,
            "hubspot_contact_id": lead.hubspot_contact_id,
            "hubspot_deal_id": lead.hubspot_deal_id,
        },
        "stage_history": stages,
        "assignment_history": assignments,
        "conversion_events": conversions,
        "follow_up_tasks": tasks,
        "tracker_events": tracker_events,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    # Audit the export
    session.add(AuditLog(
        action="gdpr_data_export",
        entity_type="lead",
        details={"lead_id": str(lead_id)},
        performed_by="system",
    ))
    await session.flush()

    logger.info("gdpr_data_exported", lead_id=str(lead_id))
    return export


async def erase_lead_data(
    session: AsyncSession,
    lead_id: UUID,
    performed_by: str = "system",
) -> dict:
    """Hard-delete PII, anonymize audit trail, remove from HubSpot (right to erasure).

    Extends the existing leads/{id}/erasure endpoint with HubSpot removal
    and more thorough data cleanup.
    """
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        return {"error": "lead_not_found"}

    hubspot_deleted = False

    # Remove from HubSpot if contact exists
    if lead.hubspot_contact_id:
        hs = HubSpotService()
        hubspot_deleted = await hs.delete_contact(lead.hubspot_contact_id)

    # Delete outreach activities via targets
    target_result = await session.execute(
        select(OutreachTarget).where(OutreachTarget.lead_id == lead_id)
    )
    targets = target_result.scalars().all()
    target_ids = [t.id for t in targets]

    activities_deleted = 0
    if target_ids:
        act_result = await session.execute(
            delete(OutreachActivity)
            .where(OutreachActivity.target_id.in_(target_ids))
            .returning(OutreachActivity.id)
        )
        activities_deleted = len(act_result.all())

    targets_deleted = 0
    if target_ids:
        tgt_result = await session.execute(
            delete(OutreachTarget)
            .where(OutreachTarget.lead_id == lead_id)
            .returning(OutreachTarget.id)
        )
        targets_deleted = len(tgt_result.all())

    # Delete tracker events
    te_result = await session.execute(
        delete(TrackerEvent)
        .where(TrackerEvent.lead_id == lead_id)
        .returning(TrackerEvent.id)
    )
    tracker_events_deleted = len(te_result.all())

    # Delete follow-up tasks
    task_result = await session.execute(
        delete(FollowUpTask)
        .where(FollowUpTask.lead_id == lead_id)
        .returning(FollowUpTask.id)
    )
    tasks_deleted = len(task_result.all())

    # Delete conversion events linked to this lead
    conv_result = await session.execute(
        delete(ConversionEvent)
        .where(ConversionEvent.lead_id == lead_id)
        .returning(ConversionEvent.id)
    )
    conv_events_deleted = len(conv_result.all())

    # Anonymize stage history
    stage_result = await session.execute(
        select(LeadStageHistory).where(LeadStageHistory.lead_id == lead_id)
    )
    for sh in stage_result.scalars().all():
        sh.changed_by = _REDACTED

    # Anonymize assignment history
    assign_result = await session.execute(
        select(LeadAssignmentHistory).where(LeadAssignmentHistory.lead_id == lead_id)
    )
    for ah in assign_result.scalars().all():
        ah.from_owner = _REDACTED
        ah.to_owner = _REDACTED
        ah.changed_by = _REDACTED

    # Redact PII on lead
    lead.first_name = _REDACTED
    lead.last_name = _REDACTED
    lead.email = _REDACTED
    lead.company = _REDACTED
    lead.message = _REDACTED
    lead.business_type = None
    lead.locations = None
    lead.interest = None
    lead.utm_source = None
    lead.utm_medium = None
    lead.utm_campaign = None
    lead.hubspot_contact_id = None
    lead.hubspot_deal_id = None
    lead.email_opt_out = True
    lead.sms_opt_out = True

    # Audit log
    session.add(AuditLog(
        action="gdpr_erasure_full",
        entity_type="lead",
        details={
            "lead_id": str(lead_id),
            "hubspot_deleted": hubspot_deleted,
            "targets_deleted": targets_deleted,
            "activities_deleted": activities_deleted,
            "tracker_events_deleted": tracker_events_deleted,
            "tasks_deleted": tasks_deleted,
            "conversion_events_deleted": conv_events_deleted,
        },
        performed_by=performed_by,
    ))
    await session.flush()

    logger.info(
        "gdpr_erasure_full_completed",
        lead_id=str(lead_id),
        hubspot_deleted=hubspot_deleted,
        performed_by=performed_by,
    )

    return {
        "lead_id": str(lead_id),
        "pii_redacted": True,
        "hubspot_deleted": hubspot_deleted,
        "targets_deleted": targets_deleted,
        "activities_deleted": activities_deleted,
        "tracker_events_deleted": tracker_events_deleted,
        "tasks_deleted": tasks_deleted,
        "conversion_events_deleted": conv_events_deleted,
    }


async def record_consent(
    session: AsyncSession,
    lead_id: UUID,
    scope: str,
    granted: bool,
    recorded_by: str = "system",
) -> dict:
    """Record consent with timestamp and scope.

    Consent is stored as an AuditLog entry with action='consent_recorded'.
    """
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        return {"error": "lead_not_found"}

    now = datetime.now(timezone.utc)
    consent_entry = AuditLog(
        action="consent_recorded",
        entity_type="lead",
        details={
            "lead_id": str(lead_id),
            "scope": scope,
            "granted": granted,
            "recorded_at": now.isoformat(),
            "recorded_by": recorded_by,
        },
        performed_by=recorded_by,
    )
    session.add(consent_entry)
    await session.flush()

    logger.info(
        "consent_recorded",
        lead_id=str(lead_id),
        scope=scope,
        granted=granted,
    )

    return {
        "lead_id": str(lead_id),
        "scope": scope,
        "granted": granted,
        "recorded_at": now.isoformat(),
    }


async def get_consent_status(session: AsyncSession, lead_id: UUID) -> dict:
    """Get the latest consent status for each scope for a lead."""
    result = await session.execute(
        select(AuditLog)
        .where(
            AuditLog.action == "consent_recorded",
            AuditLog.entity_type == "lead",
        )
        .order_by(AuditLog.created_at.desc())
    )
    logs = result.scalars().all()

    # Build consent map: latest entry per scope for this lead
    consents: dict[str, dict] = {}
    for log in logs:
        details = log.details or {}
        if details.get("lead_id") != str(lead_id):
            continue
        scope = details.get("scope", "unknown")
        if scope not in consents:
            consents[scope] = {
                "scope": scope,
                "granted": details.get("granted", False),
                "recorded_at": details.get("recorded_at"),
                "recorded_by": details.get("recorded_by", "system"),
            }

    return {
        "lead_id": str(lead_id),
        "consents": list(consents.values()),
    }


async def cleanup_expired_data(
    session: AsyncSession,
    retention_days: int = DEFAULT_DATA_RETENTION_DAYS,
) -> dict:
    """Delete data older than retention_days. Scheduled cleanup task.

    Removes:
    - TrackerEvents older than retention period
    - Conversion events older than retention period (where lead is null / already erased)
    - Old audit logs (except consent and erasure records)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    # Clean old tracker events
    te_result = await session.execute(
        delete(TrackerEvent)
        .where(TrackerEvent.occurred_at < cutoff)
        .returning(TrackerEvent.id)
    )
    tracker_deleted = len(te_result.all())

    # Clean old conversion events without lead linkage
    conv_result = await session.execute(
        delete(ConversionEvent)
        .where(
            and_(
                ConversionEvent.occurred_at < cutoff,
                ConversionEvent.lead_id.is_(None),
            )
        )
        .returning(ConversionEvent.id)
    )
    conv_deleted = len(conv_result.all())

    # Clean old audit logs (keep consent and erasure records indefinitely)
    audit_result = await session.execute(
        delete(AuditLog)
        .where(
            and_(
                AuditLog.created_at < cutoff,
                ~AuditLog.action.in_([
                    "consent_recorded",
                    "gdpr_erasure",
                    "gdpr_erasure_full",
                    "gdpr_data_export",
                ]),
            )
        )
        .returning(AuditLog.id)
    )
    audit_deleted = len(audit_result.all())

    logger.info(
        "data_retention_cleanup",
        retention_days=retention_days,
        tracker_deleted=tracker_deleted,
        conv_deleted=conv_deleted,
        audit_deleted=audit_deleted,
    )

    return {
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
        "tracker_events_deleted": tracker_deleted,
        "conversion_events_deleted": conv_deleted,
        "audit_logs_deleted": audit_deleted,
    }
