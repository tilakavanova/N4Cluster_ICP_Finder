"""Lead analytics API endpoints."""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, case, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_api_key
from src.db.models import Lead, Restaurant, ICPScore
from src.db.session import get_session
from src.utils.logging import get_logger

logger = get_logger("analytics")

router = APIRouter(prefix="/leads/analytics", tags=["analytics"], dependencies=[Depends(require_api_key)])


@router.get("/summary")
async def lead_summary(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    """Lead analytics summary: totals by source, status, and fit label."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Total leads
    total = (await session.execute(
        select(func.count(Lead.id)).where(Lead.created_at >= since)
    )).scalar() or 0

    # By source
    by_source_rows = (await session.execute(
        select(Lead.source, func.count(Lead.id))
        .where(Lead.created_at >= since)
        .group_by(Lead.source)
    )).all()

    # By status
    by_status_rows = (await session.execute(
        select(Lead.status, func.count(Lead.id))
        .where(Lead.created_at >= since)
        .group_by(Lead.status)
    )).all()

    # By fit label
    by_fit_rows = (await session.execute(
        select(Lead.icp_fit_label, func.count(Lead.id))
        .where(Lead.created_at >= since)
        .group_by(Lead.icp_fit_label)
    )).all()

    # Conversion rates (won / total)
    won = (await session.execute(
        select(func.count(Lead.id))
        .where(Lead.created_at >= since, Lead.status == "won")
    )).scalar() or 0

    # Match rate
    matched = (await session.execute(
        select(func.count(Lead.id))
        .where(Lead.created_at >= since, Lead.restaurant_id.isnot(None))
    )).scalar() or 0

    return {
        "period_days": days,
        "total_leads": total,
        "by_source": {row[0] or "unknown": row[1] for row in by_source_rows},
        "by_status": {row[0] or "unknown": row[1] for row in by_status_rows},
        "by_fit_label": {row[0] or "unscored": row[1] for row in by_fit_rows},
        "conversion_rate": round(won / total, 4) if total > 0 else 0,
        "match_rate": round(matched / total, 4) if total > 0 else 0,
    }


@router.get("/funnel")
async def lead_funnel(
    days: int = Query(30, ge=1, le=365),
    interval: str = Query("day", regex="^(day|week)$"),
    session: AsyncSession = Depends(get_session),
):
    """Leads per status over time — for funnel visualization."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    if interval == "week":
        date_expr = func.date_trunc("week", Lead.created_at)
    else:
        date_expr = cast(Lead.created_at, Date)

    rows = (await session.execute(
        select(
            date_expr.label("period"),
            Lead.status,
            func.count(Lead.id).label("count"),
        )
        .where(Lead.created_at >= since)
        .group_by("period", Lead.status)
        .order_by("period")
    )).all()

    # Group by period
    funnel: dict[str, dict[str, int]] = {}
    for row in rows:
        period_str = str(row.period)
        funnel.setdefault(period_str, {})
        funnel[period_str][row.status or "new"] = row.count

    return {
        "period_days": days,
        "interval": interval,
        "funnel": [
            {"period": k, "stages": v}
            for k, v in funnel.items()
        ],
    }


@router.get("/top-neighborhoods")
async def top_neighborhoods(
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """Leads grouped by city/zip — top neighborhoods by lead volume."""
    rows = (await session.execute(
        select(
            Restaurant.city,
            Restaurant.state,
            Restaurant.zip_code,
            func.count(Lead.id).label("lead_count"),
            func.avg(ICPScore.total_icp_score).label("avg_icp_score"),
        )
        .join(Restaurant, Lead.restaurant_id == Restaurant.id)
        .outerjoin(ICPScore, Lead.icp_score_id == ICPScore.id)
        .where(Restaurant.city.isnot(None))
        .group_by(Restaurant.city, Restaurant.state, Restaurant.zip_code)
        .order_by(func.count(Lead.id).desc())
        .limit(limit)
    )).all()

    return {
        "neighborhoods": [
            {
                "city": row.city,
                "state": row.state,
                "zip_code": row.zip_code,
                "lead_count": row.lead_count,
                "avg_icp_score": round(row.avg_icp_score, 1) if row.avg_icp_score else None,
            }
            for row in rows
        ],
    }
