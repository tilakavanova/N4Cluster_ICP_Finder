"""Outreach Orchestration & Campaign Engine API (NIF-133 through NIF-136)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_api_key
from src.db.session import get_session
from src.services.outreach import (
    create_campaign,
    get_campaign,
    list_campaigns,
    update_campaign,
    delete_campaign,
    select_targets,
    add_target,
    list_targets,
    update_target_status,
    log_activity,
    list_activities,
    calculate_performance,
)
from src.utils.logging import get_logger

logger = get_logger("outreach_api")

router = APIRouter(
    prefix="/outreach",
    tags=["outreach"],
    dependencies=[Depends(require_api_key)],
)


# ── Pydantic schemas ──────────────────────────────────────────────


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    campaign_type: str = Field(default="email", pattern="^(email|call|sms|multi)$")
    target_criteria: dict = Field(default_factory=dict)
    start_date: datetime | None = None
    end_date: datetime | None = None
    created_by: str = "system"


class CampaignUpdate(BaseModel):
    name: str | None = None
    campaign_type: str | None = Field(default=None, pattern="^(email|call|sms|multi)$")
    status: str | None = Field(default=None, pattern="^(draft|active|paused|completed)$")
    target_criteria: dict | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


class TargetAdd(BaseModel):
    restaurant_id: UUID
    lead_id: UUID | None = None
    priority: int = 0
    assigned_to: str | None = None


class TargetSelect(BaseModel):
    min_icp_score: float | None = None
    zip_codes: list[str] | None = None
    cuisines: list[str] | None = None
    limit: int = Field(default=200, ge=1, le=1000)


class ActivityCreate(BaseModel):
    activity_type: str = Field(pattern="^(email_sent|call_made|sms_sent|meeting|note)$")
    outcome: str | None = Field(default=None, pattern="^(no_answer|interested|not_interested|callback|converted)$")
    notes: str | None = None
    performed_by: str = "system"


# ── Campaign CRUD endpoints ──────────────────────────────────────


@router.post("/campaigns")
async def create_campaign_endpoint(
    body: CampaignCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a new outreach campaign (NIF-133)."""
    campaign = await create_campaign(
        session,
        name=body.name,
        campaign_type=body.campaign_type,
        target_criteria=body.target_criteria,
        start_date=body.start_date,
        end_date=body.end_date,
        created_by=body.created_by,
    )
    await session.commit()
    return _campaign_to_dict(campaign)


@router.get("/campaigns")
async def list_campaigns_endpoint(
    status: str | None = Query(None, pattern="^(draft|active|paused|completed)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """List outreach campaigns."""
    campaigns = await list_campaigns(session, status=status, limit=limit, offset=offset)
    return [_campaign_to_dict(c) for c in campaigns]


@router.get("/campaigns/{campaign_id}")
async def get_campaign_endpoint(
    campaign_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get a single outreach campaign."""
    campaign = await get_campaign(session, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return _campaign_to_dict(campaign)


@router.patch("/campaigns/{campaign_id}")
async def update_campaign_endpoint(
    campaign_id: UUID,
    body: CampaignUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update an outreach campaign."""
    try:
        campaign = await update_campaign(
            session,
            campaign_id,
            **body.model_dump(exclude_none=True),
        )
        await session.commit()
        return _campaign_to_dict(campaign)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign_endpoint(
    campaign_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Delete an outreach campaign and all related data."""
    deleted = await delete_campaign(session, campaign_id)
    if not deleted:
        raise HTTPException(404, "Campaign not found")
    await session.commit()
    return {"deleted": True}


# ── Target endpoints ─────────────────────────────────────────────


@router.post("/campaigns/{campaign_id}/targets/select")
async def select_targets_endpoint(
    campaign_id: UUID,
    body: TargetSelect,
    session: AsyncSession = Depends(get_session),
):
    """Select targets for a campaign by ICP score, zip code, cuisine (NIF-134)."""
    try:
        targets = await select_targets(
            session,
            campaign_id,
            min_icp_score=body.min_icp_score,
            zip_codes=body.zip_codes,
            cuisines=body.cuisines,
            limit=body.limit,
        )
        await session.commit()
        return {"selected": len(targets), "targets": [_target_to_dict(t) for t in targets]}
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/campaigns/{campaign_id}/targets")
async def add_target_endpoint(
    campaign_id: UUID,
    body: TargetAdd,
    session: AsyncSession = Depends(get_session),
):
    """Manually add a target to a campaign."""
    try:
        target = await add_target(
            session,
            campaign_id,
            restaurant_id=body.restaurant_id,
            lead_id=body.lead_id,
            priority=body.priority,
            assigned_to=body.assigned_to,
        )
        await session.commit()
        return _target_to_dict(target)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/campaigns/{campaign_id}/targets")
async def list_targets_endpoint(
    campaign_id: UUID,
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """List targets for a campaign."""
    targets = await list_targets(session, campaign_id, status=status, limit=limit, offset=offset)
    return [_target_to_dict(t) for t in targets]


@router.patch("/targets/{target_id}/status")
async def update_target_status_endpoint(
    target_id: UUID,
    status: str = Query(..., pattern="^(pending|contacted|responded|converted|skipped)$"),
    session: AsyncSession = Depends(get_session),
):
    """Update a target's status."""
    try:
        target = await update_target_status(session, target_id, status)
        await session.commit()
        return _target_to_dict(target)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


# ── Activity endpoints ───────────────────────────────────────────


@router.post("/targets/{target_id}/activities")
async def log_activity_endpoint(
    target_id: UUID,
    body: ActivityCreate,
    session: AsyncSession = Depends(get_session),
):
    """Log an outreach activity for a target (NIF-135)."""
    try:
        activity = await log_activity(
            session,
            target_id,
            activity_type=body.activity_type,
            outcome=body.outcome,
            notes=body.notes,
            performed_by=body.performed_by,
        )
        await session.commit()
        return _activity_to_dict(activity)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/targets/{target_id}/activities")
async def list_activities_endpoint(
    target_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """List activities for a target."""
    activities = await list_activities(session, target_id)
    return [_activity_to_dict(a) for a in activities]


# ── Performance endpoints ────────────────────────────────────────


@router.post("/campaigns/{campaign_id}/performance")
async def calculate_performance_endpoint(
    campaign_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Calculate and return performance summary for a campaign (NIF-136)."""
    try:
        perf = await calculate_performance(session, campaign_id)
        await session.commit()
        return _performance_to_dict(perf)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/campaigns/{campaign_id}/performance")
async def get_performance_endpoint(
    campaign_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get the latest performance summary for a campaign."""
    from sqlalchemy import select
    from src.db.models import OutreachPerformance

    result = await session.execute(
        select(OutreachPerformance).where(OutreachPerformance.campaign_id == campaign_id)
    )
    perf = result.scalar_one_or_none()
    if not perf:
        raise HTTPException(404, "No performance data found. Calculate performance first.")
    return _performance_to_dict(perf)


# ── Helpers ───────────────────────────────────────────────────────


def _campaign_to_dict(c) -> dict:
    return {
        "id": str(c.id),
        "name": c.name,
        "campaign_type": c.campaign_type,
        "status": c.status,
        "target_criteria": c.target_criteria,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "created_by": c.created_by,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _target_to_dict(t) -> dict:
    return {
        "id": str(t.id),
        "campaign_id": str(t.campaign_id),
        "restaurant_id": str(t.restaurant_id),
        "lead_id": str(t.lead_id) if t.lead_id else None,
        "status": t.status,
        "priority": t.priority,
        "assigned_to": t.assigned_to,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _activity_to_dict(a) -> dict:
    return {
        "id": str(a.id),
        "target_id": str(a.target_id),
        "activity_type": a.activity_type,
        "outcome": a.outcome,
        "notes": a.notes,
        "performed_by": a.performed_by,
        "performed_at": a.performed_at.isoformat() if a.performed_at else None,
    }


def _performance_to_dict(p) -> dict:
    return {
        "id": str(p.id),
        "campaign_id": str(p.campaign_id),
        "total_targets": p.total_targets,
        "contacted": p.contacted,
        "responded": p.responded,
        "converted": p.converted,
        "response_rate": p.response_rate,
        "conversion_rate": p.conversion_rate,
        "last_calculated_at": p.last_calculated_at.isoformat() if p.last_calculated_at else None,
    }
