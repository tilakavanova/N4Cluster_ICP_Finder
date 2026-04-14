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
    def test_high_review_count(self):
        assert engagement_recency_score(review_count=1000) == 1.0

    def test_medium_review_count(self):
        assert engagement_recency_score(review_count=200) == 0.8

    def test_low_review_count(self):
        assert engagement_recency_score(review_count=50) == 0.6

    def test_very_low_review_count(self):
        assert engagement_recency_score(review_count=10) == 0.4

    def test_no_reviews(self):
        assert engagement_recency_score(review_count=0) == 0.1

    def test_high_rating_boost(self):
        """4.5+ rating with 100+ reviews gets +0.1 boost."""
        base = engagement_recency_score(review_count=200, rating=4.0)
        boosted = engagement_recency_score(review_count=200, rating=4.5)
        assert boosted > base

    def test_review_date_overrides_count(self):
        """If review date is available, it takes precedence."""
        recent = datetime.now(timezone.utc) - timedelta(days=5)
        assert engagement_recency_score(review_count=0, latest_review_date=recent) == 1.0

    def test_old_review_date(self):
        old = datetime.now(timezone.utc) - timedelta(days=365)
        assert engagement_recency_score(review_count=1000, latest_review_date=old) == 0.1


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


# ── Coverage gap tests ────────────────────────────────────────────────────────

class TestDeliveryDetectionEdgeCases:
    def test_unknown_platform_name_preserved(self):
        """A delivery platform that isn't doordash/uber/grubhub is kept as-is."""
        records = [{"source": "yelp", "has_delivery": True, "delivery_platform": "caviar"}]
        has_delivery, platforms, count = detect_delivery(records)
        assert has_delivery is True
        assert "caviar" in platforms
        assert count == 1

    def test_extracted_data_delivery_platforms(self):
        """delivery_platforms list inside extracted_data is also scanned."""
        records = [
            {
                "source": "google_maps",
                "extracted_data": {"delivery_platforms": ["doordash", "grubhub"]},
            }
        ]
        has_delivery, platforms, count = detect_delivery(records)
        assert has_delivery is True
        assert "doordash" in platforms
        assert "grubhub" in platforms
        assert count == 2


class TestPOSDetectionEdgeCases:
    def test_has_pos_flag_in_extracted_data(self):
        """detect_pos returns True when extracted_data.has_pos is set."""
        has_pos, provider = detect_pos("", {"has_pos": True, "pos_provider": "Toast"})
        assert has_pos is True
        assert provider == "Toast"

    def test_pos_indicators_in_extracted_data(self):
        """POS detected via pos_indicators list in extracted_data."""
        has_pos, provider = detect_pos("", {"pos_indicators": ["square terminal"]})
        assert has_pos is True
        assert provider == "Square"

    def test_pos_indicators_empty_list(self):
        """Empty pos_indicators list yields no POS detection."""
        has_pos, provider = detect_pos("", {"pos_indicators": []})
        assert has_pos is False

    def test_pos_via_extracted_has_pos_no_provider(self):
        """has_pos=True with no provider returns (True, None)."""
        has_pos, provider = detect_pos("", {"has_pos": True})
        assert has_pos is True
        assert provider is None


class TestPricePointEdgeCases:
    def test_non_dollar_string_returns_default(self):
        """A price_tier string with no '$' chars falls through to the 0.7 default."""
        assert price_point_score("moderate") == 0.7

    def test_empty_string_returns_default(self):
        """An empty price string is falsy → 0.7."""
        assert price_point_score("") == 0.7


class TestEngagementRecencyEdgeCases:
    def test_naive_datetime_handled(self):
        """Naive datetime (no tzinfo) should be treated as UTC and not crash."""
        recent_naive = datetime.now() - timedelta(days=10)
        score = engagement_recency_score(review_count=0, latest_review_date=recent_naive)
        assert score == 1.0

    def test_review_date_60_days_ago(self):
        """31-90 days → 0.7."""
        date_60 = datetime.now(timezone.utc) - timedelta(days=60)
        assert engagement_recency_score(latest_review_date=date_60) == 0.7

    def test_review_date_120_days_ago(self):
        """91-180 days → 0.4."""
        date_120 = datetime.now(timezone.utc) - timedelta(days=120)
        assert engagement_recency_score(latest_review_date=date_120) == 0.4

    def test_review_date_exactly_30_days(self):
        """Exactly 30 days old → 1.0 (boundary)."""
        date_30 = datetime.now(timezone.utc) - timedelta(days=30)
        assert engagement_recency_score(latest_review_date=date_30) == 1.0

    def test_review_date_exactly_90_days(self):
        """Exactly 90 days old → 0.7 (boundary)."""
        date_90 = datetime.now(timezone.utc) - timedelta(days=90)
        assert engagement_recency_score(latest_review_date=date_90) == 0.7

    def test_review_date_exactly_180_days(self):
        """Exactly 180 days old → 0.4 (boundary)."""
        date_180 = datetime.now(timezone.utc) - timedelta(days=180)
        assert engagement_recency_score(latest_review_date=date_180) == 0.4

    def test_review_count_below_10_with_no_date(self):
        """review_count < 10 with no date → 0.1."""
        assert engagement_recency_score(review_count=9) == 0.1

    def test_review_count_500_with_high_rating_capped_at_one(self):
        """500+ reviews + 4.5 rating: boost applies but caps at 1.0."""
        score = engagement_recency_score(review_count=500, rating=4.5)
        assert score == 1.0
