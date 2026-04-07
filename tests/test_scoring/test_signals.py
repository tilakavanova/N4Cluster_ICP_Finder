"""Tests for ICP scoring signals v2."""

from datetime import datetime, timedelta, timezone

from src.scoring.signals import (
    detect_chain, detect_pos, detect_delivery, normalize_review_signal,
    platform_dependency_score, pos_maturity_score, volume_proxy_score,
    cuisine_fit_score, price_point_score, engagement_recency_score,
    compute_disqualifier_penalty,
)


class TestChainDetection:
    def test_known_chain_mcdonalds(self):
        is_chain, name = detect_chain("McDonald's Downtown")
        assert is_chain is True

    def test_independent_restaurant(self):
        is_chain, name = detect_chain("Joe's Corner Pizza")
        assert is_chain is False

    def test_chain_from_extracted_data(self):
        is_chain, name = detect_chain("Some Place", {"is_chain": True, "chain_name": "FranchiseCo"})
        assert is_chain is True

    def test_empty_name(self):
        is_chain, _ = detect_chain("")
        assert is_chain is False


class TestDeliveryDetection:
    def test_doordash_source(self):
        records = [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}]
        has_delivery, platforms, count = detect_delivery(records)
        assert has_delivery is True
        assert "doordash" in platforms
        assert count == 1

    def test_multiple_platforms(self):
        records = [
            {"source": "doordash"},
            {"source": "ubereats"},
            {"source": "yelp", "extracted_data": {"delivery_platforms": ["grubhub"]}},
        ]
        has_delivery, platforms, count = detect_delivery(records)
        assert has_delivery is True
        assert count == 3

    def test_no_delivery(self):
        has_delivery, platforms, count = detect_delivery([{"source": "google_maps"}])
        assert has_delivery is False
        assert count == 0

    def test_empty_records(self):
        has_delivery, platforms, count = detect_delivery([])
        assert has_delivery is False
        assert count == 0


class TestPlatformDependencyScore:
    def test_three_plus_platforms(self):
        assert platform_dependency_score(3) == 1.0
        assert platform_dependency_score(4) == 1.0

    def test_two_platforms(self):
        assert platform_dependency_score(2) == 0.75

    def test_one_platform(self):
        assert platform_dependency_score(1) == 0.5

    def test_no_platforms(self):
        assert platform_dependency_score(0) == 0.0


class TestPOSDetection:
    def test_toast_detected(self):
        has_pos, provider = detect_pos("Order online via Toast")
        assert has_pos is True
        assert provider == "Toast"

    def test_no_pos(self):
        has_pos, provider = detect_pos("Welcome to our restaurant")
        assert has_pos is False


class TestPOSMaturity:
    def test_modern_pos_toast(self):
        assert pos_maturity_score(True, "Toast") == 1.0

    def test_modern_pos_square(self):
        assert pos_maturity_score(True, "Square") == 1.0

    def test_legacy_pos_aloha(self):
        assert pos_maturity_score(True, "Aloha (NCR)") == 0.5

    def test_unknown_pos(self):
        assert pos_maturity_score(True, "SomeUnknownPOS") == 0.7

    def test_no_pos(self):
        assert pos_maturity_score(False, None) == 0.3


class TestVolumeProxy:
    def test_high_volume(self):
        score = volume_proxy_score(1000, 4.5)
        assert score >= 0.9

    def test_medium_volume(self):
        score = volume_proxy_score(200, 4.0)
        assert 0.6 <= score <= 0.9

    def test_low_volume(self):
        score = volume_proxy_score(10, 3.0)
        assert score < 0.5

    def test_zero_reviews(self):
        assert volume_proxy_score(0, 0.0) == 0.0

    def test_capped_at_one(self):
        assert volume_proxy_score(100000, 5.0) <= 1.0


class TestCuisineFit:
    def test_normal_cuisine(self):
        assert cuisine_fit_score(["Pizza", "Italian"]) == 1.0

    def test_fine_dining_penalized(self):
        assert cuisine_fit_score(["Fine Dining", "French"]) == 0.2

    def test_omakase_penalized(self):
        assert cuisine_fit_score(["Omakase", "Japanese"]) == 0.2

    def test_four_dollar_price_penalized(self):
        assert cuisine_fit_score(["French"], "$$$$") == 0.2

    def test_unknown_cuisine(self):
        assert cuisine_fit_score([]) == 0.8

    def test_normal_with_price(self):
        assert cuisine_fit_score(["Mexican"], "$$") == 1.0


class TestPricePoint:
    def test_ideal_two_dollars(self):
        assert price_point_score("$$") == 1.0

    def test_budget_one_dollar(self):
        assert price_point_score("$") == 0.7

    def test_upscale_three_dollars(self):
        assert price_point_score("$$$") == 0.5

    def test_fine_dining_four_dollars(self):
        assert price_point_score("$$$$") == 0.1

    def test_unknown_price(self):
        assert price_point_score(None) == 0.7


class TestEngagementRecency:
    def test_recent_review(self):
        recent = datetime.now(timezone.utc) - timedelta(days=5)
        assert engagement_recency_score(recent) == 1.0

    def test_review_60_days(self):
        d = datetime.now(timezone.utc) - timedelta(days=60)
        assert engagement_recency_score(d) == 0.7

    def test_review_120_days(self):
        d = datetime.now(timezone.utc) - timedelta(days=120)
        assert engagement_recency_score(d) == 0.4

    def test_old_review(self):
        d = datetime.now(timezone.utc) - timedelta(days=365)
        assert engagement_recency_score(d) == 0.1

    def test_no_review_date(self):
        assert engagement_recency_score(None) == 0.3


class TestDisqualifiers:
    def test_national_chain_penalty(self):
        p = compute_disqualifier_penalty(True, False, True, 100)
        assert p == 30.0

    def test_fine_dining_penalty(self):
        p = compute_disqualifier_penalty(False, True, True, 100)
        assert p == 15.0

    def test_no_delivery_penalty(self):
        p = compute_disqualifier_penalty(False, False, False, 100)
        assert p == 20.0

    def test_low_reviews_penalty(self):
        p = compute_disqualifier_penalty(False, False, True, 5)
        assert p == 10.0

    def test_multiple_penalties_stack(self):
        p = compute_disqualifier_penalty(True, True, False, 3)
        assert p == 30 + 15 + 20 + 10  # 75

    def test_no_penalties(self):
        p = compute_disqualifier_penalty(False, False, True, 100)
        assert p == 0.0


class TestReviewSignalLegacy:
    def test_high_reviews(self):
        score = normalize_review_signal(1000, 4.5)
        assert 0.0 <= score <= 1.0

    def test_zero_reviews(self):
        score = normalize_review_signal(0, 0.0)
        assert score >= 0.0
