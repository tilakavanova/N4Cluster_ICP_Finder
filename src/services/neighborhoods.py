"""Neighborhood Opportunity Engine — scoring, ranking, comparison (NIF-118-121)."""

from collections import Counter

from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Restaurant, ICPScore, Neighborhood
from src.utils.logging import get_logger

logger = get_logger("neighborhoods")


async def compute_neighborhood_score(
    session: AsyncSession,
    zip_code: str,
) -> dict:
    """Compute opportunity score for a neighborhood (zip code).

    Scores 0-100 based on:
    - Restaurant density (count)
    - Average ICP score of restaurants
    - Independence ratio (% non-chain)
    - Delivery coverage (% with delivery)
    """
    # Get restaurants in this zip code
    result = await session.execute(
        select(
            func.count(Restaurant.id).label("count"),
            func.avg(Restaurant.lat).label("avg_lat"),
            func.avg(Restaurant.lng).label("avg_lng"),
            func.min(Restaurant.city).label("city"),
            func.min(Restaurant.state).label("state"),
            func.sum(case((Restaurant.is_chain == False, 1), else_=0)).label("independent_count"),  # noqa: E712
        ).where(Restaurant.zip_code == zip_code)
    )
    row = result.one()

    if row.count == 0:
        return None

    # ICP scores for this zip
    score_result = await session.execute(
        select(func.avg(ICPScore.total_icp_score))
        .join(Restaurant, ICPScore.restaurant_id == Restaurant.id)
        .where(Restaurant.zip_code == zip_code)
    )
    avg_icp = score_result.scalar() or 0.0

    # Delivery coverage
    delivery_result = await session.execute(
        select(func.count())
        .select_from(ICPScore)
        .join(Restaurant, ICPScore.restaurant_id == Restaurant.id)
        .where(Restaurant.zip_code == zip_code, ICPScore.has_delivery == True)  # noqa: E712
    )
    delivery_count = delivery_result.scalar() or 0

    # Top cuisines
    cuisine_result = await session.execute(
        select(func.unnest(Restaurant.cuisine_type).label("cuisine"))
        .where(Restaurant.zip_code == zip_code)
    )
    cuisines = [r.cuisine for r in cuisine_result.all() if r.cuisine]
    top_cuisines = [c for c, _ in Counter(cuisines).most_common(5)]

    restaurant_count = row.count
    independent_ratio = (row.independent_count or 0) / restaurant_count if restaurant_count else 0
    delivery_coverage = delivery_count / restaurant_count if restaurant_count else 0

    # Opportunity score: weighted composite
    # Higher score = better opportunity for N4Cluster
    density_signal = min(restaurant_count / 50.0, 1.0) * 25  # up to 25 pts
    icp_signal = (avg_icp / 100.0) * 30  # up to 30 pts
    independence_signal = independent_ratio * 25  # up to 25 pts
    delivery_signal = delivery_coverage * 20  # up to 20 pts
    opportunity_score = round(density_signal + icp_signal + independence_signal + delivery_signal, 2)

    return {
        "zip_code": zip_code,
        "name": f"{row.city or 'Unknown'}, {row.state or '??'} {zip_code}",
        "city": row.city,
        "state": row.state,
        "lat": round(row.avg_lat, 6) if row.avg_lat else None,
        "lng": round(row.avg_lng, 6) if row.avg_lng else None,
        "restaurant_count": restaurant_count,
        "avg_icp_score": round(avg_icp, 2),
        "top_cuisines": top_cuisines,
        "independent_ratio": round(independent_ratio, 4),
        "delivery_coverage": round(delivery_coverage, 4),
        "opportunity_score": opportunity_score,
    }


async def refresh_neighborhood(session: AsyncSession, zip_code: str) -> Neighborhood | None:
    """Recompute and upsert a neighborhood record."""
    data = await compute_neighborhood_score(session, zip_code)
    if not data:
        return None

    result = await session.execute(
        select(Neighborhood).where(Neighborhood.zip_code == zip_code)
    )
    neighborhood = result.scalar_one_or_none()

    if neighborhood:
        for key, value in data.items():
            if hasattr(neighborhood, key):
                setattr(neighborhood, key, value)
    else:
        neighborhood = Neighborhood(**data)
        session.add(neighborhood)

    await session.flush()
    return neighborhood


async def rank_neighborhoods(
    session: AsyncSession,
    state: str | None = None,
    city: str | None = None,
    min_restaurants: int = 3,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Rank neighborhoods by opportunity score (NIF-120)."""
    query = (
        select(Neighborhood)
        .where(Neighborhood.restaurant_count >= min_restaurants)
        .order_by(Neighborhood.opportunity_score.desc())
    )
    if state:
        query = query.where(Neighborhood.state == state.upper())
    if city:
        query = query.where(Neighborhood.city.ilike(f"%{city}%"))
    query = query.offset(offset).limit(limit)

    result = await session.execute(query)
    neighborhoods = result.scalars().all()

    return [
        {
            "rank": offset + i + 1,
            "zip_code": n.zip_code,
            "name": n.name,
            "city": n.city,
            "state": n.state,
            "restaurant_count": n.restaurant_count,
            "avg_icp_score": n.avg_icp_score,
            "opportunity_score": n.opportunity_score,
            "independent_ratio": n.independent_ratio,
            "delivery_coverage": n.delivery_coverage,
            "top_cuisines": n.top_cuisines or [],
        }
        for i, n in enumerate(neighborhoods)
    ]


async def compare_neighborhoods(
    session: AsyncSession,
    zip_codes: list[str],
) -> dict:
    """Compare multiple neighborhoods side by side (NIF-121)."""
    result = await session.execute(
        select(Neighborhood).where(Neighborhood.zip_code.in_(zip_codes))
    )
    neighborhoods = {n.zip_code: n for n in result.scalars().all()}

    comparisons = []
    for zc in zip_codes:
        n = neighborhoods.get(zc)
        if n:
            comparisons.append({
                "zip_code": n.zip_code,
                "name": n.name,
                "restaurant_count": n.restaurant_count,
                "avg_icp_score": n.avg_icp_score,
                "opportunity_score": n.opportunity_score,
                "independent_ratio": n.independent_ratio,
                "delivery_coverage": n.delivery_coverage,
                "top_cuisines": n.top_cuisines or [],
            })
        else:
            comparisons.append({"zip_code": zc, "error": "not_found"})

    # Determine winner in each category
    valid = [c for c in comparisons if "error" not in c]
    winners = {}
    if valid:
        for metric in ["opportunity_score", "avg_icp_score", "restaurant_count", "independent_ratio", "delivery_coverage"]:
            best = max(valid, key=lambda x: x.get(metric, 0))
            winners[metric] = best["zip_code"]

    return {
        "neighborhoods": comparisons,
        "winners": winners,
    }


async def refresh_all_neighborhoods(session: AsyncSession) -> int:
    """Recompute scores for all zip codes with restaurants."""
    result = await session.execute(
        select(Restaurant.zip_code)
        .where(Restaurant.zip_code.isnot(None))
        .distinct()
    )
    zip_codes = [r[0] for r in result.all()]

    count = 0
    for zc in zip_codes:
        n = await refresh_neighborhood(session, zc)
        if n:
            count += 1

    logger.info("neighborhoods_refreshed", total=count)
    return count
