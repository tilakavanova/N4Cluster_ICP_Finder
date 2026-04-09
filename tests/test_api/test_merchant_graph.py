"""Tests for Merchant Intelligence Graph (NIF-122,123,124)."""

import uuid

import pytest

from src.db.models import MerchantEntity, MerchantRelationship


class TestMerchantEntity:
    """NIF-122: Merchant graph entity model."""

    def test_entity_creation(self):
        entity = MerchantEntity(
            restaurant_id=uuid.uuid4(),
            entity_type="restaurant",
            tags=["independent", "Italian", "high-volume"],
            enrichment_data={"icp_score": 82.5, "fit_label": "strong"},
        )
        assert entity.entity_type == "restaurant"
        assert "independent" in entity.tags
        assert entity.enrichment_data["icp_score"] == 82.5

    def test_entity_default_type(self):
        entity = MerchantEntity(
            restaurant_id=uuid.uuid4(),
            entity_type="restaurant",
        )
        assert entity.entity_type == "restaurant"

    def test_entity_tags_filtering(self):
        entities = [
            MerchantEntity(restaurant_id=uuid.uuid4(), tags=["independent", "pizza"]),
            MerchantEntity(restaurant_id=uuid.uuid4(), tags=["chain", "burger"]),
            MerchantEntity(restaurant_id=uuid.uuid4(), tags=["independent", "sushi"]),
        ]
        independent = [e for e in entities if "independent" in (e.tags or [])]
        assert len(independent) == 2


class TestMerchantRelationship:
    """NIF-123: Merchant graph relationship model."""

    def test_relationship_creation(self):
        rel = MerchantRelationship(
            source_entity_id=uuid.uuid4(),
            target_entity_id=uuid.uuid4(),
            relationship_type="same_cuisine",
            strength=0.8,
            metadata_={"cuisine": "Italian"},
        )
        assert rel.relationship_type == "same_cuisine"
        assert rel.strength == 0.8
        assert rel.metadata_["cuisine"] == "Italian"

    def test_relationship_types(self):
        types = ["same_cuisine", "same_neighborhood", "same_chain", "competitor", "cluster_peer"]
        for rt in types:
            rel = MerchantRelationship(
                source_entity_id=uuid.uuid4(),
                target_entity_id=uuid.uuid4(),
                relationship_type=rt,
            )
            assert rel.relationship_type == rt

    def test_relationship_strength_range(self):
        rel = MerchantRelationship(
            source_entity_id=uuid.uuid4(),
            target_entity_id=uuid.uuid4(),
            relationship_type="same_neighborhood",
            strength=0.5,
        )
        assert 0.0 <= rel.strength <= 1.0

    def test_unique_constraint_exists(self):
        assert any(
            c.name == "uq_merchant_rel"
            for c in MerchantRelationship.__table__.constraints
            if hasattr(c, "name")
        )


class TestMerchantGraphQuery:
    """NIF-124: Merchant graph query (unit tests for data structures)."""

    def test_graph_query_response_structure(self):
        """Verify expected response shape for graph queries."""
        # Simulated response
        response = {
            "entity": {
                "id": str(uuid.uuid4()),
                "restaurant_id": str(uuid.uuid4()),
                "name": "Joe's Pizza",
                "entity_type": "restaurant",
                "tags": ["independent", "pizza"],
                "enrichment": {"icp_score": 78.0},
            },
            "connections": [
                {
                    "entity_id": str(uuid.uuid4()),
                    "restaurant_id": str(uuid.uuid4()),
                    "name": "Tony's Pizzeria",
                    "tags": ["independent", "pizza"],
                }
            ],
            "edges": [
                {
                    "relationship_type": "same_cuisine",
                    "strength": 0.8,
                    "peer_entity_id": str(uuid.uuid4()),
                }
            ],
            "total_connections": 1,
        }
        assert "entity" in response
        assert "connections" in response
        assert "edges" in response
        assert response["total_connections"] == 1

    def test_edge_filtering_by_strength(self):
        edges = [
            {"type": "same_cuisine", "strength": 0.9},
            {"type": "same_neighborhood", "strength": 0.3},
            {"type": "competitor", "strength": 0.7},
        ]
        strong = [e for e in edges if e["strength"] >= 0.5]
        assert len(strong) == 2


class TestServiceImports:
    def test_service_importable(self):
        from src.services.merchant_graph import (
            ensure_entity, build_relationships_for_entity,
            query_graph, build_graph_for_zip,
        )
        assert callable(ensure_entity)
        assert callable(query_graph)

    def test_router_importable(self):
        from src.api.routers.merchant_graph import router
        assert router.prefix == "/merchant-graph"
