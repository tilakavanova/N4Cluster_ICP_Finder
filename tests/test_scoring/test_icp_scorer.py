"""Tests for the ICP scoring engine."""

from src.scoring.icp_scorer import ICPScorer


class TestICPScorer:
    def setup_method(self):
        self.scorer = ICPScorer()

    def test_perfect_score_independent(self):
        restaurant = {"name": "Joe's Pizza", "review_count": 500, "rating": 4.8}
        records = [
            {"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"},
            {
                "source": "website",
                "raw_data": {"raw_text": "Order on Toast POS"},
                "extracted_data": {"has_pos": True, "pos_provider": "Toast"},
            },
        ]
        result = self.scorer.score_restaurant(restaurant, records, density_score=0.9)
        assert result["is_independent"] is True
        assert result["has_delivery"] is True
        assert result["has_pos"] is True
        assert result["total_icp_score"] > 70

    def test_chain_scores_lower(self):
        restaurant = {"name": "McDonald's", "review_count": 500, "rating": 3.5}
        records = [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}]
        result = self.scorer.score_restaurant(restaurant, records, density_score=0.5)
        assert result["is_independent"] is False
        assert result["is_chain"] is True
        assert result["total_icp_score"] < 70

    def test_minimal_restaurant_with_no_delivery_penalized(self):
        """Independent restaurant with no delivery gets -20 penalty (no delivery disqualifier)."""
        restaurant = {"name": "Unknown Place", "review_count": 0, "rating": 0.0}
        result = self.scorer.score_restaurant(restaurant, [], density_score=0.0)
        assert result["is_independent"] is True
        # No delivery = -20 penalty, low reviews (<10) = -10 penalty
        assert result["disqualifier_penalty"] >= 20.0
        assert result["total_icp_score"] < 30
        assert result["fit_label"] == "poor"

    def test_score_range_0_to_100(self):
        restaurant = {"name": "Test", "review_count": 10000, "rating": 5.0}
        records = [
            {"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"},
            {"source": "website", "raw_data": {"raw_text": "toast"}, "extracted_data": {"has_pos": True, "pos_provider": "Toast"}},
        ]
        result = self.scorer.score_restaurant(restaurant, records, density_score=1.0)
        assert 0 <= result["total_icp_score"] <= 100

    def test_score_includes_all_required_fields(self):
        result = self.scorer.score_restaurant({"name": "T", "review_count": 0, "rating": 0}, [], 0)
        for field in ["is_independent", "is_chain", "has_delivery", "delivery_platforms",
                      "has_pos", "pos_provider", "geo_density_score", "review_volume",
                      "rating_avg", "total_icp_score", "fit_label", "scoring_version"]:
            assert field in result, f"Missing: {field}"

    def test_density_score_contributes(self):
        restaurant = {"name": "Test", "review_count": 0, "rating": 0}
        low = self.scorer.score_restaurant(restaurant, [], density_score=0.0)
        high = self.scorer.score_restaurant(restaurant, [], density_score=1.0)
        assert high["total_icp_score"] > low["total_icp_score"]

    def test_fit_classification(self):
        assert self.scorer._classify_fit(80) == "excellent"
        assert self.scorer._classify_fit(75) == "excellent"
        assert self.scorer._classify_fit(60) == "good"
        assert self.scorer._classify_fit(55) == "good"
        assert self.scorer._classify_fit(40) == "moderate"
        assert self.scorer._classify_fit(35) == "moderate"
        assert self.scorer._classify_fit(20) == "poor"
        assert self.scorer._classify_fit(0) == "poor"

    def test_batch_scoring(self):
        restaurants = [
            {"id": "1", "name": "Joe's Pizza", "lat": 40.71, "lng": -74.00, "review_count": 100, "rating": 4.0},
            {"id": "2", "name": "Subway", "lat": 40.72, "lng": -73.99, "review_count": 50, "rating": 3.0},
        ]
        sr_map = {
            "1": [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}],
            "2": [],
        }
        results = self.scorer.score_batch(restaurants, sr_map)
        assert len(results) == 2
        assert all("total_icp_score" in r for r in results)
        assert all("restaurant_id" in r for r in results)

    def test_batch_empty(self):
        assert self.scorer.score_batch([], {}) == []

    def test_batch_chain_lower_than_independent(self):
        restaurants = [
            {"id": "1", "name": "Joe's Pizza", "lat": 40.71, "lng": -74.00, "review_count": 100, "rating": 4.0},
            {"id": "2", "name": "McDonald's", "lat": 40.72, "lng": -73.99, "review_count": 100, "rating": 4.0},
        ]
        results = self.scorer.score_batch(restaurants, {"1": [], "2": []})
        assert results[0]["total_icp_score"] > results[1]["total_icp_score"]
