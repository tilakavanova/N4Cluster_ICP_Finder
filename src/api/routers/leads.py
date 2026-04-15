"""Lead management API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.api.schemas import LeadCreate, LeadUpdate, LeadResponse, LeadDetail, LeadFilter
from src.api.auth import require_auth, require_scope
from src.db.models import (
    Lead, Restaurant, ICPScore,
    OutreachTarget, OutreachActivity, TrackerEvent, AuditLog,
)
from src.db.session import get_session
from src.services.lead_enrichment import LeadEnrichmentService
from src.services.hubspot import HubSpotService
from src.services.lead_notifications import route_lead
from src.utils.logging import get_logger

logger = get_logger("leads")

router = APIRouter(prefix="/leads", tags=["leads"], dependencies=[Depends(require_auth)])


@router.post("", response_model=LeadResponse, status_code=201)
async def create_lead(request: Request, payload: LeadCreate, session: AsyncSession = Depends(get_session)):
    """Create a new lead. Deduplicates by email — updates existing lead if found."""
    # Check for existing lead with same email
    existing = await session.execute(
        select(Lead).where(func.lower(Lead.email) == payload.email.lower().strip())
    )
    lead = existing.scalar_one_or_none()

    if lead:
        # Update existing lead with new submission data
        lead.first_name = payload.first_name or lead.first_name
        lead.last_name = payload.last_name or lead.last_name
        lead.company = payload.company or lead.company
        lead.business_type = payload.business_type or lead.business_type
        lead.locations = payload.locations or lead.locations
        lead.interest = payload.interest or lead.interest
        lead.message = payload.message or lead.message
        lead.utm_source = payload.utm_source or lead.utm_source
        lead.utm_medium = payload.utm_medium or lead.utm_medium
        lead.utm_campaign = payload.utm_campaign or lead.utm_campaign
        logger.info("lead_deduplicated", email=payload.email, lead_id=str(lead.id))
    else:
        lead = Lead(
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=payload.email.lower().strip(),
            company=payload.company,
            business_type=payload.business_type,
            locations=payload.locations,
            interest=payload.interest,
            message=payload.message,
            source=payload.source,
            utm_source=payload.utm_source,
            utm_medium=payload.utm_medium,
            utm_campaign=payload.utm_campaign,
        )
        session.add(lead)
        await session.flush()

    # Match and enrich via enrichment service
    enrichment = LeadEnrichmentService(session)
    await enrichment.match_and_enrich(lead)

    # Sync to HubSpot CRM (if configured)
    hubspot = HubSpotService()
    hs_result = await hubspot.sync_lead(lead)
    if hs_result:
        lead.hubspot_contact_id = hs_result.get("hubspot_contact_id")
        lead.hubspot_deal_id = hs_result.get("hubspot_deal_id")

    # Route lead to notification channels
    routing = await route_lead(lead)

    logger.info(
        "lead_created",
        lead_id=str(lead.id),
        email=payload.email,
        source=payload.source,
        matched=lead.restaurant_id is not None,
        icp_fit=lead.icp_fit_label,
        icp_score=lead.icp_total_score,
        confidence=lead.match_confidence,
        hubspot_synced=hs_result is not None,
        routing_tier=routing["tier"],
    )
    return lead


@router.get("", response_model=list[LeadResponse])
async def list_leads(
    status: str | None = Query(None),
    source: str | None = Query(None),
    icp_fit_label: str | None = Query(None),
    email: str | None = Query(None),
    company: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """List leads with optional filters."""
    query = select(Lead).order_by(Lead.created_at.desc())

    if status:
        query = query.where(Lead.status == status)
    if source:
        query = query.where(Lead.source == source)
    if icp_fit_label:
        query = query.where(Lead.icp_fit_label == icp_fit_label)
    if email:
        query = query.where(Lead.email.ilike(f"%{email}%"))
    if company:
        query = query.where(Lead.company.ilike(f"%{company}%"))

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/{lead_id}", response_model=LeadDetail)
async def get_lead(lead_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get lead details with restaurant and ICP score data."""
    result = await session.execute(
        select(Lead)
        .options(joinedload(Lead.restaurant), joinedload(Lead.icp_score))
        .where(Lead.id == lead_id)
    )
    lead = result.unique().scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: UUID,
    payload: LeadUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update lead status or CRM references."""
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(lead, field, value)

    logger.info("lead_updated", lead_id=str(lead_id), updates=update_data)
    return lead


@router.delete("/{lead_id}/erasure", dependencies=[Depends(require_scope("admin:all"))])
async def erase_lead_pii(
    lead_id: UUID,
    auth: dict = Depends(require_scope("admin:all")),
    session: AsyncSession = Depends(get_session),
):
    """GDPR right-to-erasure: redact PII and remove related activity records.

    The Lead row is retained (business continuity / audit trail) but all
    personally-identifying fields are overwritten with "[REDACTED]".
    Related OutreachTargets, OutreachActivities, and TrackerEvents linked
    to this lead are permanently deleted.  An AuditLog entry records the
    request.

    Requires scope: admin:all
    """
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # ── 1. Cascade-delete OutreachActivities via OutreachTargets ────────────
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

    # ── 2. Delete TrackerEvents linked to this lead ──────────────────────────
    te_result = await session.execute(
        delete(TrackerEvent)
        .where(TrackerEvent.lead_id == lead_id)
        .returning(TrackerEvent.id)
    )
    tracker_events_deleted = len(te_result.all())

    # ── 3. Redact PII fields on the Lead record ──────────────────────────────
    _REDACTED = "[REDACTED]"
    lead.first_name = _REDACTED
    lead.last_name = _REDACTED
    lead.email = _REDACTED
    lead.company = _REDACTED
    lead.message = _REDACTED
    lead.utm_source = None
    lead.utm_medium = None
    lead.utm_campaign = None
    lead.hubspot_contact_id = None
    lead.hubspot_deal_id = None

    # ── 4. Write AuditLog entry ──────────────────────────────────────────────
    audit = AuditLog(
        action="gdpr_erasure",
        entity_type="lead",
        details={
            "lead_id": str(lead_id),
            "targets_deleted": targets_deleted,
            "activities_deleted": activities_deleted,
            "tracker_events_deleted": tracker_events_deleted,
        },
        performed_by=auth.get("sub", "unknown"),
    )
    session.add(audit)
    await session.flush()

    logger.info(
        "gdpr_erasure_completed",
        lead_id=str(lead_id),
        targets_deleted=targets_deleted,
        activities_deleted=activities_deleted,
        tracker_events_deleted=tracker_events_deleted,
        performed_by=auth.get("sub"),
    )

    return {
        "lead_id": str(lead_id),
        "pii_redacted": True,
        "targets_deleted": targets_deleted,
        "activities_deleted": activities_deleted,
        "tracker_events_deleted": tracker_events_deleted,
    }
