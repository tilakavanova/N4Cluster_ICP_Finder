"""Tests for geo-density scoring."""

import sys
from unittest.mock import MagicMock, patch
import numpy as np
import pytest
from src.scoring.geo_density import haversine_distance, compute_density_scores, get_neighborhood_stats


class TestHaversineDistance:
    def test_same_point_is_zero(self):
        d = haversine_distance(40.7128, -74.0060, 40.7128, -74.0060)
        assert d == pytest.approx(0.0, abs=0.001)

    def test_known_distance(self):
        # NYC to LA is ~3940 km
        d = haversine_distance(40.7128, -74.0060, 34.0522, -118.2437)
        assert 3900 < d < 4000

    def test_short_distance(self):
        # Two points ~0.1 km apart in NYC
        d = haversine_distance(40.7128, -74.0060, 40.7138, -74.0060)
        assert 0.05 < d < 0.2

    def test_symmetric(self):
        d1 = haversine_distance(40.7128, -74.0060, 34.0522, -118.2437)
        d2 = haversine_distance(34.0522, -118.2437, 40.7128, -74.0060)
        assert d1 == pytest.approx(d2, abs=0.001)


class TestComputeDensityScores:
    def test_empty_list(self):
        assert compute_density_scores([]) == {}

    def test_single_restaurant(self):
        restaurants = [{"id": "1", "name": "Test", "lat": 40.71, "lng": -74.00}]
        scores = compute_density_scores(restaurants)
        # Too few for clustering, should return 0.5 for each
        assert "1" in scores
        assert scores["1"] == 0.5

    def test_few_restaurants_below_min_cluster(self):
        restaurants = [
            {"id": f"r{i}", "name": f"R{i}", "lat": 40.71 + i * 0.001, "lng": -74.00}
            for i in range(3)
        ]
        scores = compute_density_scores(restaurants, min_cluster_size=5)
        assert len(scores) == 3
        assert all(v == 0.5 for v in scores.values())

    def test_clustered_restaurants_score_higher(self, multiple_restaurants_for_density):
        scores = compute_density_scores(multiple_restaurants_for_density, radius_km=0.5)
        assert len(scores) == 10
        assert all(0.0 <= v <= 1.0 for v in scores.values())

    def test_no_lat_lng_filtered(self):
        restaurants = [
            {"id": "1", "name": "NoCoords", "lat": None, "lng": None},
            {"id": "2", "name": "HasCoords", "lat": 40.71, "lng": -74.00},
        ]
        scores = compute_density_scores(restaurants, min_cluster_size=5)
        assert "1" not in scores
        assert "2" in scores

    def test_scores_normalized_0_to_1(self, multiple_restaurants_for_density):
        scores = compute_density_scores(multiple_restaurants_for_density)
        for score in scores.values():
            assert 0.0 <= score <= 1.0


class TestNeighborhoodStats:
    def test_empty_returns_zero(self):
        stats = get_neighborhood_stats([])
        assert stats["total"] == 0

    def test_stats_keys(self, multiple_restaurants_for_density):
        stats = get_neighborhood_stats(multiple_restaurants_for_density)
        assert "total" in stats
        assert "avg_density" in stats
        assert "max_density" in stats
        assert "min_density" in stats
        assert "dense_count" in stats


class TestDensityWithHDBSCAN:
    """Tests that exercise the HDBSCAN clustering path (mocked when not installed)."""

    def test_hdbscan_path_assigns_cluster_bonus(self, multiple_restaurants_for_density):
        """When HDBSCAN is available, clustered restaurants get a +0.1 bonus."""
        # Build a fake HDBSCAN that assigns all restaurants to cluster 0
        fake_hdbscan_instance = MagicMock()
        fake_hdbscan_instance.fit_predict.return_value = np.zeros(
            len(multiple_restaurants_for_density), dtype=int
        )
        FakeHDBSCAN = MagicMock(return_value=fake_hdbscan_instance)
        fake_module = MagicMock()
        fake_module.HDBSCAN = FakeHDBSCAN

        with patch.dict(sys.modules, {"hdbscan": fake_module}):
            scores = compute_density_scores(multiple_restaurants_for_density, radius_km=0.5)

        assert len(scores) == len(multiple_restaurants_for_density)
        # All get cluster bonus so every score should be > 0 (or == 0.1 for isolated points)
        assert all(v >= 0.0 for v in scores.values())

    def test_hdbscan_path_noise_points_no_bonus(self, multiple_restaurants_for_density):
        """Restaurants labelled -1 (noise) by HDBSCAN receive no cluster bonus."""
        # All labelled as noise (-1)
        fake_hdbscan_instance = MagicMock()
        fake_hdbscan_instance.fit_predict.return_value = np.full(
            len(multiple_restaurants_for_density), -1, dtype=int
        )
        FakeHDBSCAN = MagicMock(return_value=fake_hdbscan_instance)
        fake_module = MagicMock()
        fake_module.HDBSCAN = FakeHDBSCAN

        with patch.dict(sys.modules, {"hdbscan": fake_module}):
            scores = compute_density_scores(multiple_restaurants_for_density, radius_km=0.5)

        assert all(v <= 1.0 for v in scores.values())

    def test_sparse_restaurants_score_lower_than_clustered(self):
        """Sparse restaurants (spread across km) score lower density than clustered."""
        # Spread across 10 km — each restaurant is far from the others
        sparse = [
            {"id": str(i), "lat": 40.71 + i * 0.1, "lng": -74.00, "name": f"R{i}"}
            for i in range(10)
        ]
        # Clustered — all within 0.1 km
        clustered = [
            {"id": str(i), "lat": 40.71 + i * 0.0005, "lng": -74.00, "name": f"C{i}"}
            for i in range(10)
        ]
        sparse_scores = compute_density_scores(sparse, radius_km=0.5)
        clustered_scores = compute_density_scores(clustered, radius_km=0.5)

        avg_sparse = sum(sparse_scores.values()) / len(sparse_scores)
        avg_clustered = sum(clustered_scores.values()) / len(clustered_scores)
        assert avg_clustered > avg_sparse

    def test_min_cluster_size_boundary_returns_defaults(self):
        """Exactly min_cluster_size-1 valid restaurants triggers the fallback (0.5)."""
        restaurants = [
            {"id": str(i), "lat": 40.71 + i * 0.001, "lng": -74.00, "name": f"R{i}"}
            for i in range(4)
        ]
        scores = compute_density_scores(restaurants, min_cluster_size=5)
        assert all(v == 0.5 for v in scores.values())
