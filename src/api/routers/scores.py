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
                "rating_avg": r.rating_avg,
                "review_count": r.review_count,
                "price_tier": r.price_tier,
            },
            "score": {
                "total_icp_score": s.total_icp_score,
                "fit_label": s.fit_label,
                "scoring_version": s.scoring_version,
                "is_independent": s.is_independent,
                "has_delivery": s.has_delivery,
                "delivery_platforms": s.delivery_platforms,
                "delivery_platform_count": s.delivery_platform_count,
                "has_pos": s.has_pos,
                "pos_provider": s.pos_provider,
                "geo_density_score": s.geo_density_score,
                "volume_proxy": s.volume_proxy,
                "cuisine_fit": s.cuisine_fit,
                "price_tier": s.price_tier,
                "price_point_fit": s.price_point_fit,
                "engagement_recency": s.engagement_recency,
                "disqualifier_penalty": s.disqualifier_penalty,
            },
        }
        for r, s in rows
    ]


@router.post("/recalculate")
async def recalculate_scores(
    city: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Recalculate ICP scores inline (no Celery needed)."""
    from datetime import datetime, timezone
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from src.db.models import SourceRecord
    from src.scoring.icp_scorer import icp_scorer
    from src.scoring.geo_density import compute_density_scores

    query = select(Restaurant)
    if city:
        query = query.where(Restaurant.city.ilike(f"%{city}%"))

    result = await session.execute(query)
    restaurants = result.scalars().all()
    if not restaurants:
        return {"message": "No restaurants found", "scored": 0}

    rest_ids = [r.id for r in restaurants]
    sr_result = await session.execute(
        select(SourceRecord).where(SourceRecord.restaurant_id.in_(rest_ids))
    )
    all_records = sr_result.scalars().all()

    sr_map = {}
    for sr in all_records:
        rid = str(sr.restaurant_id)
        sr_map.setdefault(rid, []).append({
            "source": sr.source,
            "raw_data": sr.raw_data,
            "extracted_data": sr.extracted_data,
        })

    rest_dicts = [
        {"id": str(r.id), "name": r.name, "lat": r.lat, "lng": r.lng,
         "cuisine_type": r.cuisine_type or [], "review_count": r.review_count or 0,
         "rating": r.rating_avg or 0.0, "price_tier": r.price_tier}
        for r in restaurants
    ]

    density_scores = compute_density_scores(rest_dicts)
    scores = icp_scorer.score_batch(rest_dicts, sr_map, density_scores)

    for score in scores:
        values = {
            "restaurant_id": score["restaurant_id"],
            "is_independent": score["is_independent"],
            "has_delivery": score["has_delivery"],
            "delivery_platforms": score["delivery_platforms"],
            "delivery_platform_count": score.get("delivery_platform_count", 0),
            "has_pos": score["has_pos"],
            "pos_provider": score["pos_provider"],
            "geo_density_score": score["geo_density_score"],
            "review_volume": score["review_volume"],
            "rating_avg": score["rating_avg"],
            "volume_proxy": score.get("volume_proxy", 0.0),
            "cuisine_fit": score.get("cuisine_fit", 1.0),
            "price_tier": score.get("price_tier"),
            "price_point_fit": score.get("price_point_fit", 0.7),
            "engagement_recency": score.get("engagement_recency", 0.3),
            "disqualifier_penalty": score.get("disqualifier_penalty", 0.0),
            "total_icp_score": score["total_icp_score"],
            "fit_label": score["fit_label"],
            "scoring_version": score["scoring_version"],
            "scored_at": datetime.now(timezone.utc),
        }
        stmt = pg_insert(ICPScore).values(**values).on_conflict_do_update(
            index_elements=["restaurant_id"],
            set_={k: v for k, v in values.items() if k != "restaurant_id"},
        )
        await session.execute(stmt)

    await session.commit()
    return {"message": f"Scored {len(scores)} restaurants", "scored": len(scores)}


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
