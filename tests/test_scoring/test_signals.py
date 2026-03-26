"""Tests for ICP scoring signals."""

from src.scoring.signals import detect_chain, detect_pos, detect_delivery, normalize_review_signal


class TestChainDetection:
    def test_known_chain_mcdonalds(self):
        is_chain, name = detect_chain("McDonald's Downtown")
        assert is_chain is True
        assert name is not None

    def test_known_chain_subway(self):
        is_chain, _ = detect_chain("Subway #1234")
        assert is_chain is True

    def test_known_chain_case_insensitive(self):
        is_chain, _ = detect_chain("BURGER KING")
        assert is_chain is True

    def test_independent_restaurant(self):
        is_chain, name = detect_chain("Joe's Corner Pizza")
        assert is_chain is False
        assert name is None

    def test_chain_from_extracted_data(self):
        is_chain, name = detect_chain("Some Place", {"is_chain": True, "chain_name": "FranchiseCo"})
        assert is_chain is True
        assert name == "FranchiseCo"

    def test_extracted_data_not_chain(self):
        is_chain, _ = detect_chain("Some Place", {"is_chain": False})
        assert is_chain is False

    def test_empty_name(self):
        is_chain, name = detect_chain("")
        assert is_chain is False

    def test_all_known_chains_detected(self):
        chains = ["mcdonald's", "burger king", "subway", "chipotle", "starbucks", "shake shack"]
        for chain in chains:
            is_chain, _ = detect_chain(chain)
            assert is_chain is True, f"Failed to detect chain: {chain}"


class TestPOSDetection:
    def test_toast_detected(self):
        has_pos, provider = detect_pos("Order online via Toast")
        assert has_pos is True
        assert provider == "Toast"

    def test_square_detected(self):
        has_pos, provider = detect_pos("Powered by Square POS system")
        assert has_pos is True
        assert provider == "Square"

    def test_clover_detected(self):
        has_pos, provider = detect_pos("Uses Clover for payments")
        assert has_pos is True
        assert provider == "Clover"

    def test_no_pos(self):
        has_pos, provider = detect_pos("Welcome to our restaurant")
        assert has_pos is False
        assert provider is None

    def test_empty_text(self):
        has_pos, provider = detect_pos("")
        assert has_pos is False

    def test_pos_from_extracted_data(self):
        has_pos, provider = detect_pos("", {"has_pos": True, "pos_provider": "Toast"})
        assert has_pos is True
        assert provider == "Toast"

    def test_pos_from_indicators(self):
        has_pos, provider = detect_pos("", {"pos_indicators": ["Uses Toast for online ordering"]})
        assert has_pos is True
        assert provider == "Toast"


class TestDeliveryDetection:
    def test_doordash_source(self):
        records = [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}]
        has_delivery, platforms = detect_delivery(records)
        assert has_delivery is True
        assert "doordash" in platforms

    def test_ubereats_source(self):
        records = [{"source": "ubereats"}]
        has_delivery, platforms = detect_delivery(records)
        assert has_delivery is True
        assert "ubereats" in platforms

    def test_no_delivery(self):
        records = [{"source": "google_maps"}]
        has_delivery, platforms = detect_delivery(records)
        assert has_delivery is False
        assert len(platforms) == 0

    def test_empty_records(self):
        has_delivery, platforms = detect_delivery([])
        assert has_delivery is False
        assert platforms == []

    def test_delivery_from_extracted_data(self):
        records = [{"source": "yelp", "extracted_data": {"delivery_platforms": ["grubhub"]}}]
        has_delivery, platforms = detect_delivery(records)
        assert has_delivery is True
        assert "grubhub" in platforms

    def test_multiple_platforms(self):
        records = [
            {"source": "doordash"},
            {"source": "ubereats"},
            {"source": "yelp", "extracted_data": {"delivery_platforms": ["grubhub"]}},
        ]
        has_delivery, platforms = detect_delivery(records)
        assert has_delivery is True
        assert len(platforms) >= 3

    def test_has_delivery_flag(self):
        records = [{"source": "delivery", "has_delivery": True, "delivery_platform": "yelp_delivery"}]
        has_delivery, platforms = detect_delivery(records)
        assert has_delivery is True
        assert "yelp_delivery" in platforms

    def test_null_extracted_data(self):
        records = [{"source": "google_maps", "extracted_data": None}]
        has_delivery, _ = detect_delivery(records)
        assert has_delivery is False


class TestReviewSignal:
    def test_high_reviews(self):
        score = normalize_review_signal(1000, 4.5)
        assert 0.0 <= score <= 1.0
        assert score > 0.5

    def test_low_reviews(self):
        score = normalize_review_signal(5, 3.0)
        assert 0.0 <= score <= 1.0
        assert score < 0.5

    def test_zero_reviews(self):
        score = normalize_review_signal(0, 0.0)
        assert score >= 0.0

    def test_max_reviews_capped(self):
        s1 = normalize_review_signal(1000, 5.0)
        s2 = normalize_review_signal(100000, 5.0)
        assert s2 >= s1
        assert s2 <= 1.0

    def test_rating_contribution(self):
        low_rating = normalize_review_signal(100, 1.0)
        high_rating = normalize_review_signal(100, 5.0)
        assert high_rating > low_rating

    def test_volume_dominates(self):
        """Volume has 70% weight, rating 30%."""
        high_vol = normalize_review_signal(1000, 3.0)
        low_vol = normalize_review_signal(10, 5.0)
        assert high_vol > low_vol
