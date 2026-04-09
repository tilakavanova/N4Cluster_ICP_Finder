"""Merchant Intelligence Graph — entity enrichment, relationship building, queries (NIF-122-124)."""

from uuid import UUID

from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import (
    Restaurant, ICPScore, MerchantEntity, MerchantRelationship,
)
from src.utils.logging import get_logger

logger = get_logger("merchant_graph")

# Relationship types
REL_SAME_CUISINE = "same_cuisine"
REL_SAME_NEIGHBORHOOD = "same_neighborhood"
REL_SAME_CHAIN = "same_chain"
REL_COMPETITOR = "competitor"
REL_CLUSTER_PEER = "cluster_peer"


async def ensure_entity(session: AsyncSession, restaurant_id: UUID) -> MerchantEntity:
    """Get or create a merchant entity for a restaurant."""
    result = await session.execute(
        select(MerchantEntity).where(MerchantEntity.restaurant_id == restaurant_id)
    )
    entity = result.scalar_one_or_none()
    if entity:
        return entity

    # Fetch restaurant data for tagging
    restaurant = await session.get(Restaurant, restaurant_id)
    if not restaurant:
        return None

    tags = []
    if restaurant.is_chain:
        tags.append("chain")
    else:
        tags.append("independent")
    if restaurant.cuisine_type:
        tags.extend(restaurant.cuisine_type[:3])
    if restaurant.review_count and restaurant.review_count > 100:
        tags.append("high-volume")

    # Get ICP data for enrichment
    score_result = await session.execute(
        select(ICPScore).where(ICPScore.restaurant_id == restaurant_id)
    )
    icp = score_result.scalar_one_or_none()

    enrichment = {}
    if icp:
        enrichment["icp_score"] = icp.total_icp_score
        enrichment["fit_label"] = icp.fit_label
        enrichment["has_delivery"] = icp.has_delivery
        enrichment["has_pos"] = icp.has_pos
        if icp.has_delivery:
            tags.append("delivery")
        if icp.has_pos:
            tags.append("has-pos")

    entity = MerchantEntity(
        restaurant_id=restaurant_id,
        entity_type="restaurant",
        tags=tags,
        enrichment_data=enrichment,
    )
    session.add(entity)
    await session.flush()
    return entity


async def build_relationships_for_entity(
    session: AsyncSession, entity_id: UUID, max_rels: int = 20,
) -> list[MerchantRelationship]:
    """Build relationships for a merchant entity based on shared attributes."""
    entity = await session.get(MerchantEntity, entity_id)
    if not entity:
        return []

    restaurant = await session.get(Restaurant, entity.restaurant_id)
    if not restaurant:
        return []

    new_rels = []

    # Find same-neighborhood peers (same zip code)
    if restaurant.zip_code:
        peers = await session.execute(
            select(MerchantEntity)
            .join(Restaurant, MerchantEntity.restaurant_id == Restaurant.id)
            .where(
                Restaurant.zip_code == restaurant.zip_code,
                MerchantEntity.id != entity_id,
            )
            .limit(max_rels)
        )
        for peer in peers.scalars().all():
            rel = await _upsert_relationship(
                session, entity_id, peer.id, REL_SAME_NEIGHBORHOOD, 0.8,
            )
            if rel:
                new_rels.append(rel)

    # Find same-cuisine peers
    if restaurant.cuisine_type:
        for cuisine in restaurant.cuisine_type[:2]:  # top 2 cuisines
            peers = await session.execute(
                select(MerchantEntity)
                .join(Restaurant, MerchantEntity.restaurant_id == Restaurant.id)
                .where(
                    Restaurant.cuisine_type.any(cuisine),
                    MerchantEntity.id != entity_id,
                )
                .limit(max_rels)
            )
            for peer in peers.scalars().all():
                rel = await _upsert_relationship(
                    session, entity_id, peer.id, REL_SAME_CUISINE, 0.6,
                    metadata={"cuisine": cuisine},
                )
                if rel:
                    new_rels.append(rel)

    # Find same-chain peers
    if restaurant.is_chain and restaurant.chain_name:
        peers = await session.execute(
            select(MerchantEntity)
            .join(Restaurant, MerchantEntity.restaurant_id == Restaurant.id)
            .where(
                Restaurant.chain_name == restaurant.chain_name,
                MerchantEntity.id != entity_id,
            )
            .limit(max_rels)
        )
        for peer in peers.scalars().all():
            rel = await _upsert_relationship(
                session, entity_id, peer.id, REL_SAME_CHAIN, 1.0,
            )
            if rel:
                new_rels.append(rel)

    logger.info("relationships_built", entity_id=str(entity_id), count=len(new_rels))
    return new_rels


async def _upsert_relationship(
    session: AsyncSession,
    source_id: UUID, target_id: UUID,
    rel_type: str, strength: float,
    metadata: dict | None = None,
) -> MerchantRelationship | None:
    """Create relationship if it doesn't exist."""
    existing = await session.execute(
        select(MerchantRelationship).where(
            MerchantRelationship.source_entity_id == source_id,
            MerchantRelationship.target_entity_id == target_id,
            MerchantRelationship.relationship_type == rel_type,
        )
    )
    if existing.scalar_one_or_none():
        return None

    rel = MerchantRelationship(
        source_entity_id=source_id,
        target_entity_id=target_id,
        relationship_type=rel_type,
        strength=strength,
        metadata_=metadata or {},
    )
    session.add(rel)
    await session.flush()
    return rel


async def query_graph(
    session: AsyncSession,
    entity_id: UUID,
    relationship_type: str | None = None,
    min_strength: float = 0.0,
    limit: int = 50,
) -> dict:
    """Query the merchant graph for an entity's connections (NIF-124)."""
    entity = await session.get(MerchantEntity, entity_id)
    if not entity:
        return None

    restaurant = await session.get(Restaurant, entity.restaurant_id)

    # Get outgoing relationships
    query = select(MerchantRelationship).where(
        or_(
            MerchantRelationship.source_entity_id == entity_id,
            MerchantRelationship.target_entity_id == entity_id,
        ),
        MerchantRelationship.strength >= min_strength,
    )
    if relationship_type:
        query = query.where(MerchantRelationship.relationship_type == relationship_type)
    query = query.order_by(MerchantRelationship.strength.desc()).limit(limit)

    result = await session.execute(query)
    relationships = result.scalars().all()

    # Collect connected entity IDs
    connected_ids = set()
    edges = []
    for rel in relationships:
        peer_id = rel.target_entity_id if rel.source_entity_id == entity_id else rel.source_entity_id
        connected_ids.add(peer_id)
        edges.append({
            "relationship_type": rel.relationship_type,
            "strength": rel.strength,
            "peer_entity_id": str(peer_id),
            "metadata": rel.metadata_,
        })

    # Fetch connected entities with restaurants
    connected = []
    if connected_ids:
        peer_result = await session.execute(
            select(MerchantEntity)
            .options(selectinload(MerchantEntity.restaurant))
            .where(MerchantEntity.id.in_(connected_ids))
        )
        for peer in peer_result.unique().scalars().all():
            r = peer.restaurant
            connected.append({
                "entity_id": str(peer.id),
                "restaurant_id": str(peer.restaurant_id),
                "name": r.name if r else None,
                "city": r.city if r else None,
                "zip_code": r.zip_code if r else None,
                "tags": peer.tags,
                "enrichment": peer.enrichment_data,
            })

    return {
        "entity": {
            "id": str(entity.id),
            "restaurant_id": str(entity.restaurant_id),
            "name": restaurant.name if restaurant else None,
            "entity_type": entity.entity_type,
            "tags": entity.tags,
            "enrichment": entity.enrichment_data,
        },
        "connections": connected,
        "edges": edges,
        "total_connections": len(connected),
    }


async def build_graph_for_zip(session: AsyncSession, zip_code: str) -> int:
    """Build graph entities and relationships for all restaurants in a zip code."""
    result = await session.execute(
        select(Restaurant.id).where(Restaurant.zip_code == zip_code)
    )
    restaurant_ids = [r[0] for r in result.all()]

    count = 0
    for rid in restaurant_ids:
        entity = await ensure_entity(session, rid)
        if entity:
            await build_relationships_for_entity(session, entity.id)
            count += 1

    logger.info("graph_built_for_zip", zip_code=zip_code, entities=count)
    return count
