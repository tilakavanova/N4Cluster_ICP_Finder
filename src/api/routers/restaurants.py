"""Restaurant CRUD and search endpoints."""

from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.session import get_session
from src.db.models import Restaurant, ICPScore
from src.api.schemas import RestaurantResponse, RestaurantDetail, NearbyResponse
from src.utils.geo import haversine_miles, bounding_box

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


@router.get("/nearby", response_model=list[NearbyResponse])
async def nearby_restaurants(
    zip_code: str = Query(..., min_length=5, max_length=5, description="5-digit ZIP code"),
    radius: float = Query(5.0, gt=0, le=50, description="Search radius in miles"),
    cuisine: str | None = None,
    is_chain: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Find restaurants near a ZIP code within a given radius (miles).

    Looks up the centroid of the ZIP code from existing restaurant data,
    then returns all restaurants within the specified radius sorted by distance.
    """
    # Find centroid for the given zip code from existing restaurants
    centroid_query = select(
        func.avg(Restaurant.lat).label("center_lat"),
        func.avg(Restaurant.lng).label("center_lng"),
        func.count(Restaurant.id).label("cnt"),
    ).where(
        Restaurant.zip_code == zip_code,
        Restaurant.lat.isnot(None),
        Restaurant.lng.isnot(None),
    )
    centroid_result = await session.execute(centroid_query)
    row = centroid_result.one()

    if not row.center_lat or not row.center_lng:
        raise HTTPException(
            status_code=404,
            detail=f"No restaurants found with ZIP code {zip_code} to determine location.",
        )

    center_lat = float(row.center_lat)
    center_lng = float(row.center_lng)

    # Bounding box pre-filter for performance
    min_lat, max_lat, min_lng, max_lng = bounding_box(center_lat, center_lng, radius)

    query = select(Restaurant).where(
        Restaurant.lat.isnot(None),
        Restaurant.lng.isnot(None),
        Restaurant.lat.between(min_lat, max_lat),
        Restaurant.lng.between(min_lng, max_lng),
    )

    if cuisine:
        query = query.where(Restaurant.cuisine_type.any(cuisine))
    if is_chain is not None:
        query = query.where(Restaurant.is_chain == is_chain)

    result = await session.execute(query)
    candidates = result.scalars().all()

    # Precise haversine filter and distance calculation
    nearby = []
    for r in candidates:
        dist = haversine_miles(center_lat, center_lng, r.lat, r.lng)
        if dist <= radius:
            nearby.append((r, round(dist, 2)))

    # Sort by distance, apply limit
    nearby.sort(key=lambda x: x[1])
    nearby = nearby[:limit]

    return [
        NearbyResponse(
            id=r.id,
            name=r.name,
            address=r.address,
            city=r.city,
            state=r.state,
            zip_code=r.zip_code,
            lat=r.lat,
            lng=r.lng,
            phone=r.phone,
            website=r.website,
            cuisine_type=r.cuisine_type or [],
            is_chain=r.is_chain or False,
            chain_name=r.chain_name,
            created_at=r.created_at,
            updated_at=r.updated_at,
            distance_miles=dist,
        )
        for r, dist in nearby
    ]


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
