"""API endpoints for restaurant change detection."""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select, func

from src.db.session import async_session
from src.db.models import RestaurantChange, Restaurant

router = APIRouter(prefix="/changes", tags=["changes"])


@router.get("/")
async def list_changes(
    change_type: Optional[str] = Query(None, description="Filter by change type: new_restaurant, rating_change, delivery_change, field_update"),
    days: int = Query(7, ge=1, le=90, description="Look back N days"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List detected restaurant changes."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    async with async_session() as session:
        query = (
            select(RestaurantChange, Restaurant.name, Restaurant.address)
            .join(Restaurant, RestaurantChange.restaurant_id == Restaurant.id)
            .where(RestaurantChange.detected_at >= since)
            .order_by(RestaurantChange.detected_at.desc())
        )
        if change_type:
            query = query.where(RestaurantChange.change_type == change_type)

        query = query.offset(offset).limit(limit)
        result = await session.execute(query)
        rows = result.all()

        return {
            "changes": [
                {
                    "id": str(c.id),
                    "restaurant_id": str(c.restaurant_id),
                    "restaurant_name": name,
                    "restaurant_address": address,
                    "change_type": c.change_type,
                    "field_name": c.field_name,
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                    "source": c.source,
                    "detected_at": c.detected_at.isoformat(),
                }
                for c, name, address in rows
            ],
            "count": len(rows),
        }


@router.get("/summary")
async def change_summary(
    days: int = Query(7, ge=1, le=90),
):
    """Summary counts of changes by type."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    async with async_session() as session:
        result = await session.execute(
            select(
                RestaurantChange.change_type,
                func.count(RestaurantChange.id).label("count"),
            )
            .where(RestaurantChange.detected_at >= since)
            .group_by(RestaurantChange.change_type)
        )
        rows = result.all()

        return {
            "period_days": days,
            "summary": {row.change_type: row.count for row in rows},
            "total": sum(row.count for row in rows),
        }
