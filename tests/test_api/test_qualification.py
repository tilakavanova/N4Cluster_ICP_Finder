"""Tests for AI Merchant Qualification Agent (NIF-142 through NIF-144)."""

import uuid

import pytest

from src.db.models import QualificationResult, QualificationExplanation, Restaurant, ICPScore


# ── NIF-142: QualificationResult model tests ──────────────────


class TestQualificationResultModel:
    """NIF-142: AI qualification result model."""

    def test_result_creation(self):
        signals = [
            {"signal": "icp_score", "value": 0.75, "weight": 0.35, "impact": "positive"},
            {"signal": "delivery_presence", "value": 0.67, "weight": 0.20, "impact": "positive"},
        ]
        result = QualificationResult(
            restaurant_id=uuid.uuid4(),
            qualification_status="qualified",
            confidence_score=0.82,
            signals_summary=signals,
            model_version="v1",
        )
        assert result.qualification_status == "qualified"
        assert result.confidence_score == 0.82
        assert len(result.signals_summary) == 2
        assert result.model_version == "v1"

    def test_result_defaults(self):
        result = QualificationResult(restaurant_id=uuid.uuid4())
        assert result.qualification_status is None or result.qualification_status == "pending"
        assert result.confidence_score is None or result.confidence_score == 0.0

    def test_valid_statuses(self):
        for status in ["qualified", "not_qualified", "needs_review", "pending"]:
            result = QualificationResult(
                restaurant_id=uuid.uuid4(),
                qualification_status=status,
                confidence_score=0.5,
            )
            assert result.qualification_status == status

    def test_confidence_score_range(self):
        result = QualificationResult(
            restaurant_id=uuid.uuid4(),
            qualification_status="qualified",
            confidence_score=0.95,
        )
        assert 0.0 <= result.confidence_score <= 1.0

    def test_review_fields(self):
        result = QualificationResult(
            restaurant_id=uuid.uuid4(),
            qualification_status="qualified",
            confidence_score=0.9,
            reviewed_by="admin@example.com",
            review_decision="approved",
            review_notes="Verified independently",
        )
        assert result.reviewed_by == "admin@example.com"
        assert result.review_decision == "approved"
        assert result.review_notes == "Verified independently"

    def test_table_name(self):
        assert QualificationResult.__tablename__ == "qualification_results"

    def test_has_restaurant_id_fk(self):
        col = QualificationResult.__table__.c.restaurant_id
        assert len(col.foreign_keys) == 1


# ── NIF-143: QualificationExplanation model tests ─────────────


class TestQualificationExplanationModel:
    """NIF-143: Qualification explanation model."""

    def test_explanation_creation(self):
        exp = QualificationExplanation(
            result_id=uuid.uuid4(),
            factor_name="icp_score",
            factor_value="0.75",
            impact="positive",
            weight=0.35,
            explanation_text="ICP score 75.0/100 (normalized: 0.75)",
        )
        assert exp.factor_name == "icp_score"
        assert exp.factor_value == "0.75"
        assert exp.impact == "positive"
        assert exp.weight == 0.35
        assert "ICP score" in exp.explanation_text

    def test_valid_impacts(self):
        for impact in ["positive", "negative", "neutral"]:
            exp = QualificationExplanation(
                result_id=uuid.uuid4(),
                factor_name="test",
                impact=impact,
                weight=0.25,
            )
            assert exp.impact == impact

    def test_explanation_defaults(self):
        exp = QualificationExplanation(
            result_id=uuid.uuid4(),
            factor_name="test",
        )
        assert exp.impact is None or exp.impact == "neutral"
        assert exp.weight is None or exp.weight == 0.0

    def test_table_name(self):
        assert QualificationExplanation.__tablename__ == "qualification_explanations"

    def test_has_result_id_fk(self):
        col = QualificationExplanation.__table__.c.result_id
        assert len(col.foreign_keys) == 1


# ── Service logic tests ───────────────────────────────────────


class TestQualificationService:
    """Test qualification service helper functions."""

    def test_evaluate_icp_score_high(self):
        from src.services.qualification import _evaluate_icp_score
        icp = ICPScore(total_icp_score=80.0)
        value, impact, explanation = _evaluate_icp_score(icp)
        assert value == 0.8
        assert impact == "positive"
        assert "80.0" in explanation

    def test_evaluate_icp_score_medium(self):
        from src.services.qualification import _evaluate_icp_score
        icp = ICPScore(total_icp_score=50.0)
        value, impact, explanation = _evaluate_icp_score(icp)
        assert value == 0.5
        assert impact == "neutral"

    def test_evaluate_icp_score_low(self):
        from src.services.qualification import _evaluate_icp_score
        icp = ICPScore(total_icp_score=20.0)
        value, impact, explanation = _evaluate_icp_score(icp)
        assert value == 0.2
        assert impact == "negative"

    def test_evaluate_icp_score_none(self):
        from src.services.qualification import _evaluate_icp_score
        value, impact, explanation = _evaluate_icp_score(None)
        assert value == 0.0
        assert impact == "negative"

    def test_evaluate_delivery_multiple_platforms(self):
        from src.services.qualification import _evaluate_delivery
        icp = ICPScore(
            has_delivery=True,
            delivery_platform_count=3,
            delivery_platforms=["doordash", "ubereats", "grubhub"],
        )
        value, impact, explanation = _evaluate_delivery(icp)
        assert value == 1.0
        assert impact == "positive"
        assert "3 delivery platform" in explanation

    def test_evaluate_delivery_none(self):
        from src.services.qualification import _evaluate_delivery
        icp = ICPScore(has_delivery=False, delivery_platform_count=0)
        value, impact, explanation = _evaluate_delivery(icp)
        assert value == 0.0
        assert impact == "negative"

    def test_evaluate_delivery_no_icp(self):
        from src.services.qualification import _evaluate_delivery
        value, impact, explanation = _evaluate_delivery(None)
        assert value == 0.0
        assert impact == "negative"

    def test_evaluate_independence_independent(self):
        from src.services.qualification import _evaluate_independence
        restaurant = Restaurant(name="Joe's Pizza", is_chain=False)
        icp = ICPScore(is_independent=True)
        value, impact, explanation = _evaluate_independence(icp, restaurant)
        assert value == 1.0
        assert impact == "positive"
        assert "Independent" in explanation

    def test_evaluate_independence_chain(self):
        from src.services.qualification import _evaluate_independence
        restaurant = Restaurant(name="McDonald's", is_chain=True, chain_name="McDonald's")
        icp = ICPScore(is_independent=False)
        value, impact, explanation = _evaluate_independence(icp, restaurant)
        assert value == 0.0
        assert impact == "negative"
        assert "Chain" in explanation

    def test_evaluate_review_volume_high(self):
        from src.services.qualification import _evaluate_review_volume
        restaurant = Restaurant(review_count=300)
        icp = ICPScore(review_volume=300)
        value, impact, explanation = _evaluate_review_volume(icp, restaurant)
        assert value == 1.0
        assert impact == "positive"

    def test_evaluate_review_volume_low(self):
        from src.services.qualification import _evaluate_review_volume
        restaurant = Restaurant(review_count=0)
        value, impact, explanation = _evaluate_review_volume(None, restaurant)
        assert value == 0.0
        assert impact == "negative"

    def test_compute_qualification_qualified(self):
        from src.services.qualification import _compute_qualification
        restaurant = Restaurant(
            name="Great Pizza",
            is_chain=False,
            review_count=250,
            rating_avg=4.5,
        )
        icp = ICPScore(
            total_icp_score=85.0,
            has_delivery=True,
            delivery_platform_count=2,
            delivery_platforms=["doordash", "ubereats"],
            is_independent=True,
            review_volume=250,
        )
        status, confidence, signals, explanations = _compute_qualification(restaurant, icp)
        assert status == "qualified"
        assert confidence >= 0.70
        assert len(signals) == 4
        assert len(explanations) == 4

    def test_compute_qualification_not_qualified(self):
        from src.services.qualification import _compute_qualification
        restaurant = Restaurant(
            name="McDonald's",
            is_chain=True,
            chain_name="McDonald's",
            review_count=5,
        )
        icp = ICPScore(
            total_icp_score=15.0,
            has_delivery=False,
            delivery_platform_count=0,
            is_independent=False,
            review_volume=5,
        )
        status, confidence, signals, explanations = _compute_qualification(restaurant, icp)
        assert status == "not_qualified"
        assert confidence < 0.45

    def test_compute_qualification_needs_review(self):
        from src.services.qualification import _compute_qualification
        restaurant = Restaurant(
            name="Decent Place",
            is_chain=False,
            review_count=80,
        )
        icp = ICPScore(
            total_icp_score=50.0,
            has_delivery=True,
            delivery_platform_count=1,
            delivery_platforms=["doordash"],
            is_independent=True,
            review_volume=80,
        )
        status, confidence, signals, explanations = _compute_qualification(restaurant, icp)
        # With medium scores, it should be in the review or qualified range
        assert status in ("needs_review", "qualified")
        assert 0.0 <= confidence <= 1.0

    def test_signal_weights_sum_to_one(self):
        from src.services.qualification import SIGNAL_WEIGHTS
        assert abs(sum(SIGNAL_WEIGHTS.values()) - 1.0) < 0.001


# ── NIF-144: Router tests ──────────────────────────────────────


class TestQualificationRouter:
    """NIF-144: Qualification review API router."""

    def test_router_importable(self):
        from src.api.routers.qualification import router
        assert router.prefix == "/qualification"

    def test_router_has_evaluate_endpoint(self):
        from src.api.routers.qualification import router
        paths = [r.path for r in router.routes]
        assert "/qualification/evaluate/{restaurant_id}" in paths

    def test_router_has_get_endpoint(self):
        from src.api.routers.qualification import router
        paths = [r.path for r in router.routes]
        assert "/qualification/{restaurant_id}" in paths

    def test_router_has_review_endpoint(self):
        from src.api.routers.qualification import router
        paths = [r.path for r in router.routes]
        assert "/qualification/{result_id}/review" in paths

    def test_router_has_batch_endpoint(self):
        from src.api.routers.qualification import router
        paths = [r.path for r in router.routes]
        assert "/qualification/batch" in paths

    def test_router_has_pending_review_endpoint(self):
        from src.api.routers.qualification import router
        paths = [r.path for r in router.routes]
        assert "/qualification/pending-review/list" in paths

    def test_router_registered_in_app(self):
        from src.main import app
        paths = [r.path for r in app.routes]
        qualification_paths = [p for p in paths if "qualification" in p]
        assert len(qualification_paths) > 0

    def test_router_tags(self):
        from src.api.routers.qualification import router
        assert "qualification" in router.tags
