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

    # ── hot_lead flag ────────────────────────────────────────────

    def test_hot_lead_true_when_score_at_least_75(self):
        """score >= 75 must set hot_lead=True."""
        restaurant = {"name": "Joe's Pizza", "review_count": 500, "rating": 4.8}
        records = [
            {"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"},
            {"source": "ubereats", "has_delivery": True, "delivery_platform": "ubereats"},
            {
                "source": "website",
                "raw_data": {"raw_text": "powered by Toast POS"},
                "extracted_data": {"has_pos": True, "pos_provider": "Toast"},
            },
        ]
        result = self.scorer.score_restaurant(restaurant, records, density_score=1.0)
        assert result["hot_lead"] is True
        assert result["total_icp_score"] >= 75

    def test_hot_lead_false_when_score_below_75(self):
        """score < 75 must set hot_lead=False."""
        restaurant = {"name": "Unknown Place", "review_count": 5, "rating": 2.0}
        result = self.scorer.score_restaurant(restaurant, [], density_score=0.0)
        assert result["hot_lead"] is False
        assert result["total_icp_score"] < 75

    def test_hot_lead_in_result_keys(self):
        result = self.scorer.score_restaurant({"name": "T", "review_count": 0, "rating": 0}, [], 0)
        assert "hot_lead" in result

    # ── disqualifier clamping to 0 ────────────────────────────────

    def test_disqualifier_clamps_score_to_zero(self):
        """All four disqualifiers (chain + fine dining + no delivery + <10 reviews)
        total -75. Even a high base score must be clamped to 0."""
        restaurant = {
            "name": "McDonald's",
            "review_count": 1,      # <10 → -10
            "rating": 2.0,
            "cuisine_type": ["Fine Dining"],  # fine dining → -15
        }
        # no delivery records → -20; chain → -30
        result = self.scorer.score_restaurant(restaurant, [], density_score=0.0)
        assert result["total_icp_score"] == 0.0
        assert result["fit_label"] == "poor"

    # ── individual signal weight contributions ────────────────────

    def test_independence_signal_weight(self):
        """An independent restaurant should score 15 pts higher than a chain
        when all other signals are identical (all-zero base)."""
        base_records = []
        independent = {"name": "Local Spot", "review_count": 0, "rating": 0}
        chain = {"name": "McDonald's", "review_count": 0, "rating": 0}
        r_ind = self.scorer.score_restaurant(independent, base_records, 0.0)
        r_chain = self.scorer.score_restaurant(chain, base_records, 0.0)
        # Independence signal: 15 pts (weight=15, signal=1 vs 0)
        assert r_ind["total_icp_score"] > r_chain["total_icp_score"]

    def test_platform_dependency_signal_weight(self):
        """Three platforms vs zero platforms — platform_dependency weight is 20%."""
        restaurant = {"name": "Local Spot", "review_count": 0, "rating": 0}
        records_many = [
            {"source": "doordash"},
            {"source": "ubereats"},
            {"source": "grubhub"},
        ]
        score_many = self.scorer.score_restaurant(restaurant, records_many, 0.0)
        score_none = self.scorer.score_restaurant(restaurant, [], 0.0)
        assert score_many["total_icp_score"] > score_none["total_icp_score"]

    def test_volume_signal_weight(self):
        """High review count should yield a higher total score than zero reviews."""
        restaurant_high = {"name": "Local", "review_count": 1000, "rating": 4.5}
        restaurant_low = {"name": "Local", "review_count": 1, "rating": 4.5}
        r_high = self.scorer.score_restaurant(restaurant_high, [], 0.0)
        r_low = self.scorer.score_restaurant(restaurant_low, [], 0.0)
        assert r_high["total_icp_score"] > r_low["total_icp_score"]

    def test_price_signal_weight(self):
        """$$ price tier should score higher than $$$$ for the same restaurant."""
        base = {"name": "Local", "review_count": 100, "rating": 4.0}
        records = [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}]
        ideal = {**base, "price_tier": "$$"}
        fine = {**base, "price_tier": "$$$$"}
        r_ideal = self.scorer.score_restaurant(ideal, records, 0.0)
        r_fine = self.scorer.score_restaurant(fine, records, 0.0)
        assert r_ideal["total_icp_score"] > r_fine["total_icp_score"]

    def test_cuisine_signal_weight(self):
        """Fine-dining cuisine should score lower than regular cuisine."""
        base = {"name": "Local", "review_count": 100, "rating": 4.0}
        records = [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}]
        regular = {**base, "cuisine_type": ["Pizza"]}
        fine = {**base, "cuisine_type": ["Fine Dining"]}
        r_regular = self.scorer.score_restaurant(regular, records, 0.0)
        r_fine = self.scorer.score_restaurant(fine, records, 0.0)
        assert r_regular["total_icp_score"] > r_fine["total_icp_score"]

    def test_pos_signal_weight(self):
        """Modern POS should yield a higher total than no POS."""
        restaurant = {"name": "Local", "review_count": 100, "rating": 4.0}
        records_pos = [
            {"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"},
            {"source": "website", "raw_data": {"raw_text": "toast"}, "extracted_data": {}},
        ]
        records_no_pos = [
            {"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"},
        ]
        r_pos = self.scorer.score_restaurant(restaurant, records_pos, 0.0)
        r_no_pos = self.scorer.score_restaurant(restaurant, records_no_pos, 0.0)
        assert r_pos["total_icp_score"] > r_no_pos["total_icp_score"]

    def test_batch_scoring_with_precomputed_density(self):
        """score_batch should use caller-supplied density_scores dict."""
        restaurants = [
            {"id": "a", "name": "Place A", "lat": 40.71, "lng": -74.00, "review_count": 50, "rating": 4.0},
        ]
        density_scores = {"a": 0.9}
        results = self.scorer.score_batch(restaurants, {}, density_scores=density_scores)
        assert len(results) == 1
        assert results[0]["geo_density_score"] == 0.9
