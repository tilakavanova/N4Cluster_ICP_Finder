"""Tests for ICP scoring signals."""

from src.scoring.signals import detect_chain, detect_pos, detect_delivery, normalize_review_signal


class TestChainDetection:
    def test_known_chain(self):
        is_chain, name = detect_chain("McDonald's Downtown")
        assert is_chain is True
        assert name is not None

    def test_independent(self):
        is_chain, name = detect_chain("Joe's Corner Pizza")
        assert is_chain is False
        assert name is None

    def test_subway(self):
        is_chain, name = detect_chain("Subway #1234")
        assert is_chain is True

    def test_case_insensitive(self):
        is_chain, _ = detect_chain("BURGER KING")
        assert is_chain is True


class TestPOSDetection:
    def test_toast_detected(self):
        has_pos, provider = detect_pos("Order online via Toast")
        assert has_pos is True
        assert provider == "Toast"

    def test_square_detected(self):
        has_pos, provider = detect_pos("Powered by Square POS system")
        assert has_pos is True
        assert provider == "Square"

    def test_no_pos(self):
        has_pos, provider = detect_pos("Welcome to our restaurant")
        assert has_pos is False
        assert provider is None


class TestDeliveryDetection:
    def test_doordash_source(self):
        records = [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}]
        has_delivery, platforms = detect_delivery(records)
        assert has_delivery is True
        assert "doordash" in platforms

    def test_no_delivery(self):
        records = [{"source": "google_maps"}]
        has_delivery, platforms = detect_delivery(records)
        assert has_delivery is False
        assert len(platforms) == 0


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
