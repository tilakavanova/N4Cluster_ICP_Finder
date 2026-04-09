"""Neighborhood Opportunity Engine API (NIF-118,119,120,121)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_api_key
from src.db.session import get_session
from src.services.neighborhoods import (
    compute_neighborhood_score,
    rank_neighborhoods,
    compare_neighborhoods,
    refresh_neighborhood,
    refresh_all_neighborhoods,
)
from src.utils.logging import get_logger

logger = get_logger("neighborhoods")

router = APIRouter(
    prefix="/neighborhoods",
    tags=["neighborhoods"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/{zip_code}")
async def get_neighborhood_score(zip_code: str, session: AsyncSession = Depends(get_session)):
    """Get opportunity score and stats for a neighborhood (zip code)."""
    data = await compute_neighborhood_score(session, zip_code)
    if not data:
        raise HTTPException(404, f"No restaurants found in zip code {zip_code}")
    return data


@router.get("")
async def list_neighborhoods(
    state: str | None = Query(None, max_length=2, description="Filter by state code"),
    city: str | None = Query(None, description="Filter by city name"),
    min_restaurants: int = Query(3, ge=1, description="Minimum restaurant count"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Rank neighborhoods by opportunity score (NIF-120)."""
    offset = (page - 1) * page_size
    return await rank_neighborhoods(
        session, state=state, city=city,
        min_restaurants=min_restaurants, limit=page_size, offset=offset,
    )


@router.post("/compare")
async def compare(
    zip_codes: list[str] = Query(..., min_length=2, max_length=10, description="ZIP codes to compare"),
    session: AsyncSession = Depends(get_session),
):
    """Compare neighborhoods side by side (NIF-121)."""
    if len(zip_codes) < 2:
        raise HTTPException(400, "At least 2 zip codes required")
    return await compare_neighborhoods(session, zip_codes)


@router.post("/{zip_code}/refresh")
async def refresh_single(zip_code: str, session: AsyncSession = Depends(get_session)):
    """Recompute scores for a single neighborhood."""
    n = await refresh_neighborhood(session, zip_code)
    if not n:
        raise HTTPException(404, f"No restaurants found in zip code {zip_code}")
    return {"zip_code": zip_code, "opportunity_score": n.opportunity_score, "status": "refreshed"}


@router.post("/refresh-all")
async def refresh_all(session: AsyncSession = Depends(get_session)):
    """Recompute scores for all neighborhoods."""
    count = await refresh_all_neighborhoods(session)
    return {"refreshed": count, "status": "complete"}
