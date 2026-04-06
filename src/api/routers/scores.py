"""ICP score endpoints — leaderboard, filtering, export."""

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_api_key
from src.db.session import get_session
from src.db.models import Restaurant, ICPScore

router = APIRouter(prefix="/scores", tags=["scores"])


@router.get("/leaderboard")
async def leaderboard(
    city: str | None = None,
    state: str | None = None,
    fit_label: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Get top restaurants ranked by ICP score."""
    query = (
        select(Restaurant, ICPScore)
        .join(ICPScore)
        .order_by(ICPScore.total_icp_score.desc())
        .limit(limit)
    )

    if city:
        query = query.where(Restaurant.city.ilike(f"%{city}%"))
    if state:
        query = query.where(Restaurant.state == state.upper())
    if fit_label:
        query = query.where(ICPScore.fit_label == fit_label)

    result = await session.execute(query)
    rows = result.all()

    return [
        {
            "restaurant": {
                "id": str(r.id),
                "name": r.name,
                "address": r.address,
                "city": r.city,
                "state": r.state,
                "cuisine_type": r.cuisine_type,
            },
            "score": {
                "total_icp_score": s.total_icp_score,
                "fit_label": s.fit_label,
                "is_independent": s.is_independent,
                "has_delivery": s.has_delivery,
                "has_pos": s.has_pos,
                "geo_density_score": s.geo_density_score,
            },
        }
        for r, s in rows
    ]


@router.post("/recalculate")
async def recalculate_scores(
    city: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Trigger ICP score recalculation for all or filtered restaurants."""
    from src.tasks.score_tasks import score_restaurants

    query = select(Restaurant.id)
    if city:
        query = query.where(Restaurant.city.ilike(f"%{city}%"))

    result = await session.execute(query)
    ids = [str(r) for r in result.scalars().all()]

    if ids:
        score_restaurants.delay(ids)

    return {"message": f"Recalculation triggered for {len(ids)} restaurants"}


@router.get("/export", dependencies=[Depends(require_api_key)])
async def export_leads(
    format: str = Query("csv", pattern="^(csv|json)$"),
    min_score: float = Query(0.0),
    fit_label: str | None = None,
    city: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Export scored restaurants as CSV or JSON."""
    query = (
        select(Restaurant, ICPScore)
        .join(ICPScore)
        .where(ICPScore.total_icp_score >= min_score)
        .order_by(ICPScore.total_icp_score.desc())
    )

    if fit_label:
        query = query.where(ICPScore.fit_label == fit_label)
    if city:
        query = query.where(Restaurant.city.ilike(f"%{city}%"))

    result = await session.execute(query)
    rows = result.all()

    leads = []
    for r, s in rows:
        leads.append({
            "name": r.name,
            "address": r.address,
            "city": r.city,
            "state": r.state,
            "zip_code": r.zip_code,
            "phone": r.phone,
            "website": r.website,
            "cuisine_type": ", ".join(r.cuisine_type or []),
            "icp_score": s.total_icp_score,
            "fit_label": s.fit_label,
            "is_independent": s.is_independent,
            "has_delivery": s.has_delivery,
            "delivery_platforms": ", ".join(s.delivery_platforms or []),
            "has_pos": s.has_pos,
            "pos_provider": s.pos_provider,
            "geo_density_score": s.geo_density_score,
        })

    if format == "json":
        return leads

    # CSV export
    if not leads:
        return StreamingResponse(io.StringIO(""), media_type="text/csv")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=leads[0].keys())
    writer.writeheader()
    writer.writerows(leads)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=icp_leads.csv"},
    )
