"""Conversion Intelligence & Neighborhood Penetration Analytics API (NIF-148, NIF-149, NIF-150)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_api_key
from src.db.session import get_session
from src.services.conversion_analytics import (
    record_event,
    get_funnel,
    calculate_funnel,
    get_conversion_timeline,
    get_funnel_trends,
)
from src.utils.logging import get_logger

logger = get_logger("conversion_analytics_api")

router = APIRouter(
    prefix="/conversion",
    tags=["conversion"],
    dependencies=[Depends(require_api_key)],
)


# -- Pydantic schemas ---------------------------------------------------------


class RecordEventBody(BaseModel):
    restaurant_id: UUID
    event_type: str = Field(..., pattern="^(discovered|contacted|demo_scheduled|pilot_started|converted|churned)$")
    source: str | None = None
    lead_id: UUID | None = None
    metadata: dict | None = None


class CalculateFunnelBody(BaseModel):
    period: str
    zip_code: str | None = None


# -- Helpers -------------------------------------------------------------------


def _event_to_dict(event) -> dict:
    return {
        "id": str(event.id),
        "restaurant_id": str(event.restaurant_id),
        "lead_id": str(event.lead_id) if event.lead_id else None,
        "event_type": event.event_type,
        "source": event.source,
        "metadata": event.metadata_,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
    }


def _funnel_to_dict(funnel) -> dict:
    return {
        "id": str(funnel.id),
        "period": funnel.period,
        "zip_code": funnel.zip_code,
        "discovered": funnel.discovered,
        "contacted": funnel.contacted,
        "demo_scheduled": funnel.demo_scheduled,
        "pilot_started": funnel.pilot_started,
        "converted": funnel.converted,
        "churned": funnel.churned,
        "conversion_rate": funnel.conversion_rate,
        "avg_days_to_convert": funnel.avg_days_to_convert,
        "last_calculated_at": funnel.last_calculated_at.isoformat() if funnel.last_calculated_at else None,
    }


# -- Endpoints -----------------------------------------------------------------


@router.post("/events")
async def create_conversion_event(
    body: RecordEventBody,
    session: AsyncSession = Depends(get_session),
):
    """Record a conversion funnel event (NIF-148)."""
    try:
        event = await record_event(
            session,
            restaurant_id=body.restaurant_id,
            event_type=body.event_type,
            source=body.source,
            lead_id=body.lead_id,
            metadata=body.metadata,
        )
        await session.commit()
        return _event_to_dict(event)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/events/{restaurant_id}")
async def get_restaurant_timeline(
    restaurant_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get conversion event timeline for a restaurant (NIF-150)."""
    events = await get_conversion_timeline(session, restaurant_id)
    return [_event_to_dict(e) for e in events]


@router.get("/funnel")
async def get_funnel_summary(
    period: str = Query(..., description="Period string, e.g. '2026-W15' or '2026-04'"),
    zip_code: str | None = Query(None, description="Optional zip code filter"),
    session: AsyncSession = Depends(get_session),
):
    """Get funnel summary for a period (NIF-149)."""
    funnel = await get_funnel(session, period, zip_code)
    if not funnel:
        raise HTTPException(404, f"No funnel data for period '{period}'" + (f" zip_code '{zip_code}'" if zip_code else ""))
    return _funnel_to_dict(funnel)


@router.get("/trends")
async def get_trends(
    periods: str = Query(..., description="Comma-separated period strings"),
    zip_code: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Get funnel trends across multiple periods (NIF-150)."""
    period_list = [p.strip() for p in periods.split(",") if p.strip()]
    if not period_list:
        raise HTTPException(400, "At least one period is required")
    funnels = await get_funnel_trends(session, period_list, zip_code)
    return [_funnel_to_dict(f) for f in funnels]


@router.post("/funnel/calculate")
async def trigger_funnel_calculation(
    body: CalculateFunnelBody,
    session: AsyncSession = Depends(get_session),
):
    """Trigger funnel recalculation for a period (NIF-149)."""
    funnel = await calculate_funnel(session, body.period, body.zip_code)
    await session.commit()
    return _funnel_to_dict(funnel)
