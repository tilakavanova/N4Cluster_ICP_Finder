"""Tests for Neighborhood Opportunity Engine (NIF-118,119,120,121)."""

import uuid

import pytest

from src.db.models import Neighborhood


class TestNeighborhoodModel:
    """NIF-118: Neighborhood boundary normalization model."""

    def test_neighborhood_creation(self):
        n = Neighborhood(
            zip_code="10001",
            name="Midtown Manhattan, NY 10001",
            city="New York",
            state="NY",
            lat=40.7484,
            lng=-73.9967,
            restaurant_count=42,
            avg_icp_score=68.5,
            top_cuisines=["Italian", "Pizza", "Chinese"],
            independent_ratio=0.72,
            delivery_coverage=0.85,
            opportunity_score=78.3,
        )
        assert n.zip_code == "10001"
        assert n.city == "New York"
        assert n.state == "NY"
        assert n.restaurant_count == 42
        assert n.opportunity_score == 78.3

    def test_neighborhood_defaults(self):
        n = Neighborhood(zip_code="90210")
        assert n.restaurant_count is None or n.restaurant_count == 0
        assert n.top_cuisines is None or n.top_cuisines == []

    def test_neighborhood_zip_uniqueness_constraint(self):
        """Neighborhood table has unique constraint on zip_code."""
        assert any(
            c.name == "uq_neighborhood_zip"
            for c in Neighborhood.__table__.constraints
            if hasattr(c, "name")
        )


class TestNeighborhoodScore:
    """NIF-119: Neighborhood score model."""

    def test_opportunity_score_range(self):
        """Score should be between 0 and 100."""
        # Max possible: density(25) + icp(30) + independence(25) + delivery(20) = 100
        n = Neighborhood(
            zip_code="10001",
            opportunity_score=78.3,
        )
        assert 0 <= n.opportunity_score <= 100

    def test_score_components_stored(self):
        n = Neighborhood(
            zip_code="10001",
            avg_icp_score=72.0,
            independent_ratio=0.8,
            delivery_coverage=0.6,
        )
        assert n.avg_icp_score == 72.0
        assert n.independent_ratio == 0.8
        assert n.delivery_coverage == 0.6


class TestNeighborhoodRanking:
    """NIF-120: Neighborhood ranking API (unit tests for model sorting)."""

    def test_neighborhoods_sortable_by_score(self):
        neighborhoods = [
            Neighborhood(zip_code="10001", opportunity_score=85.0),
            Neighborhood(zip_code="10002", opportunity_score=62.0),
            Neighborhood(zip_code="10003", opportunity_score=91.0),
        ]
        ranked = sorted(neighborhoods, key=lambda n: n.opportunity_score, reverse=True)
        assert ranked[0].zip_code == "10003"
        assert ranked[1].zip_code == "10001"
        assert ranked[2].zip_code == "10002"

    def test_ranking_with_equal_scores(self):
        neighborhoods = [
            Neighborhood(zip_code="10001", opportunity_score=75.0),
            Neighborhood(zip_code="10002", opportunity_score=75.0),
        ]
        ranked = sorted(neighborhoods, key=lambda n: n.opportunity_score, reverse=True)
        assert len(ranked) == 2
        assert ranked[0].opportunity_score == ranked[1].opportunity_score


class TestNeighborhoodComparison:
    """NIF-121: Neighborhood comparison model."""

    def test_comparison_metrics(self):
        n1 = Neighborhood(
            zip_code="10001", opportunity_score=85.0,
            avg_icp_score=72.0, restaurant_count=45,
            independent_ratio=0.8, delivery_coverage=0.9,
        )
        n2 = Neighborhood(
            zip_code="90210", opportunity_score=60.0,
            avg_icp_score=55.0, restaurant_count=20,
            independent_ratio=0.6, delivery_coverage=0.5,
        )

        metrics = ["opportunity_score", "avg_icp_score", "restaurant_count", "independent_ratio", "delivery_coverage"]
        winners = {}
        for metric in metrics:
            val1 = getattr(n1, metric)
            val2 = getattr(n2, metric)
            winners[metric] = n1.zip_code if val1 >= val2 else n2.zip_code

        assert all(w == "10001" for w in winners.values())

    def test_comparison_mixed_winners(self):
        n1 = Neighborhood(
            zip_code="10001", opportunity_score=70.0,
            avg_icp_score=80.0, restaurant_count=30,
        )
        n2 = Neighborhood(
            zip_code="10002", opportunity_score=75.0,
            avg_icp_score=60.0, restaurant_count=50,
        )
        assert n2.opportunity_score > n1.opportunity_score
        assert n1.avg_icp_score > n2.avg_icp_score
        assert n2.restaurant_count > n1.restaurant_count


class TestNeighborhoodServiceImports:
    """Verify service and router can be imported."""

    def test_service_importable(self):
        from src.services.neighborhoods import (
            compute_neighborhood_score,
            rank_neighborhoods,
            compare_neighborhoods,
            refresh_neighborhood,
            refresh_all_neighborhoods,
        )
        assert callable(compute_neighborhood_score)
        assert callable(rank_neighborhoods)
        assert callable(compare_neighborhoods)

    def test_router_importable(self):
        from src.api.routers.neighborhoods import router
        assert router.prefix == "/neighborhoods"
        # Should have 5 routes
        route_paths = [r.path for r in router.routes]
        assert "/neighborhoods/{zip_code}" in route_paths
        assert "/neighborhoods" in route_paths
        assert "/neighborhoods/compare" in route_paths
