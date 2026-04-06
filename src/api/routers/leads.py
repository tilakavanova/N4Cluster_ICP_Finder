"""Lead management API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.api.schemas import LeadCreate, LeadUpdate, LeadResponse, LeadDetail, LeadFilter
from src.api.auth import require_api_key
from src.db.models import Lead, Restaurant, ICPScore
from src.db.session import get_session
from src.services.lead_enrichment import LeadEnrichmentService
from src.utils.logging import get_logger

logger = get_logger("leads")

router = APIRouter(prefix="/leads", tags=["leads"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=LeadResponse, status_code=201)
async def create_lead(payload: LeadCreate, session: AsyncSession = Depends(get_session)):
    """Create a new lead. Auto-matches against restaurant DB and enriches with ICP score."""
    lead = Lead(
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
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

    logger.info(
        "lead_created",
        lead_id=str(lead.id),
        email=payload.email,
        source=payload.source,
        matched=lead.restaurant_id is not None,
        icp_fit=lead.icp_fit_label,
        icp_score=lead.icp_total_score,
        confidence=lead.match_confidence,
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
