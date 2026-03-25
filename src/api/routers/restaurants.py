"""Restaurant CRUD and search endpoints."""

from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.session import get_session
from src.db.models import Restaurant, ICPScore
from src.api.schemas import RestaurantResponse, RestaurantDetail

router = APIRouter(prefix="/restaurants", tags=["restaurants"])


@router.get("", response_model=list[RestaurantResponse])
async def list_restaurants(
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    cuisine: str | None = None,
    min_score: float | None = None,
    is_chain: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """List restaurants with optional filters."""
    query = select(Restaurant)

    if city:
        query = query.where(Restaurant.city.ilike(f"%{city}%"))
    if state:
        query = query.where(Restaurant.state == state.upper())
    if zip_code:
        query = query.where(Restaurant.zip_code == zip_code)
    if cuisine:
        query = query.where(Restaurant.cuisine_type.any(cuisine))
    if is_chain is not None:
        query = query.where(Restaurant.is_chain == is_chain)
    if min_score is not None:
        query = query.join(ICPScore).where(ICPScore.total_icp_score >= min_score)

    query = query.offset((page - 1) * page_size).limit(page_size)
    query = query.order_by(Restaurant.updated_at.desc())

    result = await session.execute(query)
    return result.scalars().all()


@router.get("/search", response_model=list[RestaurantResponse])
async def search_restaurants(
    q: str = Query(..., min_length=2, description="Search query"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Full-text search for restaurants by name or address."""
    query = (
        select(Restaurant)
        .where(
            (Restaurant.name.ilike(f"%{q}%"))
            | (Restaurant.address.ilike(f"%{q}%"))
            | (Restaurant.city.ilike(f"%{q}%"))
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/{restaurant_id}", response_model=RestaurantDetail)
async def get_restaurant(
    restaurant_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get full restaurant details with source records and ICP score."""
    query = (
        select(Restaurant)
        .options(
            selectinload(Restaurant.source_records),
            selectinload(Restaurant.icp_score),
        )
        .where(Restaurant.id == restaurant_id)
    )
    result = await session.execute(query)
    restaurant = result.scalar_one_or_none()

    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    return restaurant
