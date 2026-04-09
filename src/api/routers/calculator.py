"""Savings calculator API powered by ICP data."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Restaurant, ICPScore, SourceRecord
from src.db.session import get_session
from src.utils.logging import get_logger

logger = get_logger("calculator")

router = APIRouter(prefix="/leads/calculator", tags=["calculator"])

# Industry average fallbacks
DEFAULTS = {
    "avg_order_value": 35.0,
    "monthly_orders": 800,
    "platforms": {
        "doordash": {"name": "DoorDash", "commission_pct": 0.25},
        "ubereats": {"name": "Uber Eats", "commission_pct": 0.30},
        "grubhub": {"name": "Grubhub", "commission_pct": 0.20},
    },
    "n4cluster_fee_per_order": 0.99,
}


def _estimate_avg_order_value(price_tier: str | None) -> float:
    """Estimate average order value from price tier."""
    return {
        "$": 18.0,
        "$$": 32.0,
        "$$$": 55.0,
        "$$$$": 85.0,
    }.get(price_tier or "", DEFAULTS["avg_order_value"])


def _estimate_monthly_orders(review_count: int | None, rating: float | None) -> int:
    """Rough monthly order estimate from review volume and rating."""
    if not review_count:
        return DEFAULTS["monthly_orders"]
    # Heuristic: ~1 review per 30 orders, scaled by rating
    rating_mult = (rating or 4.0) / 4.0
    estimated = int(review_count * 2.5 * rating_mult)
    return max(100, min(estimated, 5000))


@router.get("")
async def savings_calculator(
    company: str = Query(..., min_length=1, description="Restaurant/company name"),
    city: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Return pre-populated calculator data for a known restaurant.

    Falls back to industry averages if restaurant not in database.
    Used by the N4ClusterDotcom pricing page interactive calculator.
    """
    # Try to find restaurant in DB
    query = select(Restaurant).where(
        func.lower(Restaurant.name).contains(company.lower().strip())
    )
    if city:
        query = query.where(func.lower(Restaurant.city) == city.lower().strip())
    query = query.limit(1)

    result = await session.execute(query)
    restaurant = result.scalar_one_or_none()

    if restaurant:
        # Get ICP score for delivery platform info
        score_result = await session.execute(
            select(ICPScore).where(ICPScore.restaurant_id == restaurant.id)
        )
        icp_score = score_result.scalar_one_or_none()

        # Get source records for delivery platform details
        sr_result = await session.execute(
            select(SourceRecord)
            .where(SourceRecord.restaurant_id == restaurant.id)
            .where(SourceRecord.source.in_(["doordash", "ubereats", "grubhub", "delivery"]))
        )
        delivery_records = sr_result.scalars().all()

        avg_order = _estimate_avg_order_value(restaurant.price_tier)
        monthly_orders = _estimate_monthly_orders(restaurant.review_count, restaurant.rating_avg)

        # Build detected platforms
        detected_platforms = []
        if icp_score and icp_score.delivery_platforms:
            for p in icp_score.delivery_platforms:
                p_lower = p.lower()
                default = DEFAULTS["platforms"].get(p_lower)
                detected_platforms.append({
                    "id": p_lower,
                    "name": default["name"] if default else p,
                    "commission_pct": default["commission_pct"] if default else 0.25,
                })

        # If no platforms detected from ICP, check source records
        if not detected_platforms and delivery_records:
            seen = set()
            for sr in delivery_records:
                src = sr.source.lower()
                if src not in seen and src in DEFAULTS["platforms"]:
                    seen.add(src)
                    default = DEFAULTS["platforms"][src]
                    detected_platforms.append({
                        "id": src,
                        "name": default["name"],
                        "commission_pct": default["commission_pct"],
                    })

        # Calculate savings
        monthly_revenue = avg_order * monthly_orders
        total_commission = sum(
            monthly_revenue * p["commission_pct"] / max(len(detected_platforms), 1)
            for p in detected_platforms
        ) if detected_platforms else monthly_revenue * 0.25
        n4_monthly_cost = monthly_orders * DEFAULTS["n4cluster_fee_per_order"]
        monthly_savings = total_commission - n4_monthly_cost

        return {
            "matched": True,
            "restaurant_name": restaurant.name,
            "city": restaurant.city,
            "state": restaurant.state,
            "avg_order_value": avg_order,
            "estimated_monthly_orders": monthly_orders,
            "detected_platforms": detected_platforms,
            "n4cluster_fee_per_order": DEFAULTS["n4cluster_fee_per_order"],
            "estimated_monthly_commission": round(total_commission, 2),
            "estimated_n4_monthly_cost": round(n4_monthly_cost, 2),
            "estimated_monthly_savings": round(monthly_savings, 2),
            "estimated_annual_savings": round(monthly_savings * 12, 2),
            "icp_score": icp_score.total_icp_score if icp_score else None,
            "icp_fit": icp_score.fit_label if icp_score else None,
        }

    # Fallback: industry averages
    avg_order = DEFAULTS["avg_order_value"]
    monthly_orders = DEFAULTS["monthly_orders"]
    monthly_revenue = avg_order * monthly_orders
    avg_commission = monthly_revenue * 0.25
    n4_cost = monthly_orders * DEFAULTS["n4cluster_fee_per_order"]

    return {
        "matched": False,
        "restaurant_name": None,
        "city": city,
        "state": None,
        "avg_order_value": avg_order,
        "estimated_monthly_orders": monthly_orders,
        "detected_platforms": [
            {"id": k, "name": v["name"], "commission_pct": v["commission_pct"]}
            for k, v in DEFAULTS["platforms"].items()
        ],
        "n4cluster_fee_per_order": DEFAULTS["n4cluster_fee_per_order"],
        "estimated_monthly_commission": round(avg_commission, 2),
        "estimated_n4_monthly_cost": round(n4_cost, 2),
        "estimated_monthly_savings": round(avg_commission - n4_cost, 2),
        "estimated_annual_savings": round((avg_commission - n4_cost) * 12, 2),
        "icp_score": None,
        "icp_fit": None,
    }
