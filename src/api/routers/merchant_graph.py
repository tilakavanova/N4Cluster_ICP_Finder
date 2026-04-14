"""Merchant Intelligence Graph API (NIF-122,123,124)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.db.models import MerchantEntity
from src.db.session import get_session
from src.services.merchant_graph import (
    ensure_entity,
    build_relationships_for_entity,
    query_graph,
    build_graph_for_zip,
)
from src.utils.logging import get_logger

logger = get_logger("merchant_graph")

router = APIRouter(
    prefix="/merchant-graph",
    tags=["merchant-graph"],
    dependencies=[Depends(require_auth)],
)


@router.get("/entities/{entity_id}")
async def get_entity_graph(
    entity_id: UUID,
    relationship_type: str | None = Query(None, description="Filter by relationship type"),
    min_strength: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Query the merchant graph for an entity's connections (NIF-124)."""
    result = await query_graph(session, entity_id, relationship_type, min_strength, limit)
    if not result:
        raise HTTPException(404, "Entity not found")
    return result


@router.get("/by-restaurant/{restaurant_id}")
async def get_graph_by_restaurant(
    restaurant_id: UUID,
    relationship_type: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Query the graph starting from a restaurant ID."""
    result = await session.execute(
        select(MerchantEntity).where(MerchantEntity.restaurant_id == restaurant_id)
    )
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(404, "No graph entity for this restaurant. Use POST /build to create one.")
    graph = await query_graph(session, entity.id, relationship_type)
    return graph


@router.post("/build/{restaurant_id}")
async def build_entity(
    restaurant_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Create or update a merchant entity and build its relationships."""
    entity = await ensure_entity(session, restaurant_id)
    if not entity:
        raise HTTPException(404, "Restaurant not found")
    rels = await build_relationships_for_entity(session, entity.id)
    return {
        "entity_id": str(entity.id),
        "restaurant_id": str(restaurant_id),
        "tags": entity.tags,
        "relationships_created": len(rels),
    }


@router.post("/build-zip/{zip_code}")
async def build_zip_graph(zip_code: str, session: AsyncSession = Depends(get_session)):
    """Build graph for all restaurants in a zip code."""
    count = await build_graph_for_zip(session, zip_code)
    if count == 0:
        raise HTTPException(404, f"No restaurants found in {zip_code}")
    return {"zip_code": zip_code, "entities_processed": count}


@router.get("/entities")
async def list_entities(
    tag: str | None = Query(None, description="Filter by tag"),
    entity_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """List merchant entities with optional filters."""
    query = select(MerchantEntity).order_by(MerchantEntity.created_at.desc())
    if tag:
        query = query.where(MerchantEntity.tags.any(tag))
    if entity_type:
        query = query.where(MerchantEntity.entity_type == entity_type)
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await session.execute(query)
    entities = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "restaurant_id": str(e.restaurant_id),
            "entity_type": e.entity_type,
            "tags": e.tags,
            "enrichment": e.enrichment_data,
        }
        for e in entities
    ]
