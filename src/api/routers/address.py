"""Address normalization API (NIF-263)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.db.session import get_session
from src.services.address_normalization import (
    normalize_address,
    geocode_restaurant,
    batch_normalize,
)
from src.utils.logging import get_logger

logger = get_logger("address_api")

router = APIRouter(
    prefix="/address",
    tags=["address"],
    dependencies=[Depends(require_auth)],
)


class NormalizeBody(BaseModel):
    address: str
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None


class BatchNormalizeBody(BaseModel):
    limit: int = 100


@router.post("/normalize")
async def normalize_single_address(body: NormalizeBody):
    """Normalize a single address using Google Geocoding API (NIF-263)."""
    result = await normalize_address(
        address=body.address,
        city=body.city,
        state=body.state,
        zip_code=body.zip_code,
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/batch-normalize")
async def trigger_batch_normalize(
    body: BatchNormalizeBody,
    session: AsyncSession = Depends(get_session),
):
    """Trigger batch normalization of restaurants with missing/inconsistent addresses (NIF-263)."""
    result = await batch_normalize(session, limit=body.limit)
    await session.commit()
    return result


@router.get("/geocode/{restaurant_id}")
async def geocode_single_restaurant(
    restaurant_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Geocode a specific restaurant (NIF-263)."""
    result = await geocode_restaurant(session, restaurant_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    await session.commit()
    return result
