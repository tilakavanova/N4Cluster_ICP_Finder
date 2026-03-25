"""Tests for the ICP scoring engine."""

from src.scoring.icp_scorer import ICPScorer


class TestICPScorer:
    def setup_method(self):
        self.scorer = ICPScorer()

    def test_perfect_score_independent(self):
        restaurant = {
            "name": "Joe's Pizza",
            "review_count": 500,
            "rating": 4.8,
        }
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
        assert result["total_icp_score"] > 70

    def test_chain_scores_lower(self):
        restaurant = {
            "name": "McDonald's",
            "review_count": 500,
            "rating": 3.5,
        }
        records = [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}]
        result = self.scorer.score_restaurant(restaurant, records, density_score=0.5)

        assert result["is_independent"] is False
        assert result["total_icp_score"] < 70

    def test_fit_classification(self):
        assert self.scorer._classify_fit(80) == "excellent"
        assert self.scorer._classify_fit(60) == "good"
        assert self.scorer._classify_fit(40) == "moderate"
        assert self.scorer._classify_fit(20) == "poor"

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
