"""Tests for Configurable Scoring Engine (NIF-125 through NIF-132)."""

import uuid

import pytest

from src.db.models import (
    ScoringProfile, ScoringRule, ScoreExplanation,
    ScoreVersion, ScoringConfigLink, ScoreRecalcJob,
)


class TestScoringProfile:
    """NIF-125: Scoring profile model."""

    def test_profile_creation(self):
        signals = [
            {"name": "independent", "weight": 15, "type": "boolean", "enabled": True},
            {"name": "platform_dependency", "weight": 20, "type": "numeric", "enabled": True},
            {"name": "volume", "weight": 15, "type": "numeric", "enabled": True},
        ]
        profile = ScoringProfile(
            name="Default ICP v3",
            version=1,
            description="Standard ICP scoring profile",
            signals=signals,
            is_active=True,
        )
        assert profile.name == "Default ICP v3"
        assert profile.version == 1
        assert len(profile.signals) == 3
        assert profile.signals[0]["name"] == "independent"
        assert profile.is_active is True

    def test_profile_defaults(self):
        profile = ScoringProfile(name="Minimal")
        assert profile.version is None or profile.version == 1  # Column default
        assert profile.is_active is None or profile.is_active is True

    def test_profile_signal_weights_sum(self):
        signals = [
            {"name": "independent", "weight": 15, "type": "boolean", "enabled": True},
            {"name": "platform_dependency", "weight": 20, "type": "numeric", "enabled": True},
            {"name": "pos", "weight": 12, "type": "numeric", "enabled": True},
            {"name": "density", "weight": 12, "type": "numeric", "enabled": True},
            {"name": "volume", "weight": 15, "type": "numeric", "enabled": True},
            {"name": "cuisine_fit", "weight": 10, "type": "numeric", "enabled": True},
            {"name": "price_point", "weight": 8, "type": "numeric", "enabled": True},
            {"name": "engagement", "weight": 8, "type": "numeric", "enabled": True},
        ]
        total_weight = sum(s["weight"] for s in signals)
        assert total_weight == 100

    def test_profile_table_name(self):
        assert ScoringProfile.__tablename__ == "scoring_profiles"


class TestScoringRule:
    """NIF-126: Scoring rule model."""

    def test_rule_creation_threshold(self):
        rule = ScoringRule(
            profile_id=uuid.uuid4(),
            signal_name="volume",
            rule_type="threshold",
            condition={"min": 200},
            points=5.0,
            description="Bonus for high-volume restaurants",
        )
        assert rule.rule_type == "threshold"
        assert rule.condition["min"] == 200
        assert rule.points == 5.0

    def test_rule_creation_range(self):
        rule = ScoringRule(
            profile_id=uuid.uuid4(),
            signal_name="density",
            rule_type="range",
            condition={"min": 0.3, "max": 0.8},
            points=3.0,
        )
        assert rule.rule_type == "range"
        assert rule.condition["min"] == 0.3

    def test_rule_creation_boolean(self):
        rule = ScoringRule(
            profile_id=uuid.uuid4(),
            signal_name="independent",
            rule_type="boolean",
            condition={"expected": True},
            points=10.0,
        )
        assert rule.rule_type == "boolean"
        assert rule.condition["expected"] is True

    def test_rule_types(self):
        for rt in ["threshold", "range", "boolean", "custom"]:
            rule = ScoringRule(
                profile_id=uuid.uuid4(),
                signal_name="test",
                rule_type=rt,
                points=1.0,
            )
            assert rule.rule_type == rt

    def test_rule_table_name(self):
        assert ScoringRule.__tablename__ == "scoring_rules"


class TestScoreExplanation:
    """NIF-127 + NIF-131: Score explanation and storage."""

    def test_explanation_creation(self):
        breakdown = [
            {"signal": "independent", "raw_value": 1.0, "weighted_value": 15.0, "explanation": "Independent restaurant"},
            {"signal": "volume", "raw_value": 0.7, "weighted_value": 10.5, "explanation": "200 reviews, 4.2 avg"},
        ]
        exp = ScoreExplanation(
            restaurant_id=uuid.uuid4(),
            profile_id=uuid.uuid4(),
            signal_breakdown=breakdown,
            total_score=72.5,
            fit_label="good",
            explanation_text="independent: 1.00 x 15 = 15.00; volume: 0.70 x 15 = 10.50",
        )
        assert exp.total_score == 72.5
        assert exp.fit_label == "good"
        assert len(exp.signal_breakdown) == 2
        assert exp.signal_breakdown[0]["signal"] == "independent"

    def test_fit_labels(self):
        labels = {"excellent", "good", "moderate", "poor", "unknown"}
        for label in labels:
            exp = ScoreExplanation(
                restaurant_id=uuid.uuid4(),
                profile_id=uuid.uuid4(),
                fit_label=label,
                total_score=50.0,
            )
            assert exp.fit_label in labels

    def test_explanation_table_name(self):
        assert ScoreExplanation.__tablename__ == "score_explanations"


class TestScoreVersion:
    """NIF-129: Score version tracking."""

    def test_version_creation(self):
        sv = ScoreVersion(
            profile_id=uuid.uuid4(),
            version_number=2,
            changes={"signals": {"old": [], "new": [{"name": "test"}]}},
            created_by="admin",
        )
        assert sv.version_number == 2
        assert "signals" in sv.changes
        assert sv.created_by == "admin"

    def test_version_table_name(self):
        assert ScoreVersion.__tablename__ == "score_versions"


class TestScoringConfigLink:
    """NIF-130: Scoring configuration linking."""

    def test_config_link_market(self):
        link = ScoringConfigLink(
            profile_id=uuid.uuid4(),
            entity_type="market",
            entity_value="NYC",
        )
        assert link.entity_type == "market"
        assert link.entity_value == "NYC"

    def test_config_link_cuisine(self):
        link = ScoringConfigLink(
            profile_id=uuid.uuid4(),
            entity_type="cuisine",
            entity_value="Italian",
        )
        assert link.entity_type == "cuisine"

    def test_config_link_chain_group(self):
        link = ScoringConfigLink(
            profile_id=uuid.uuid4(),
            entity_type="chain_group",
            entity_value="fast-casual",
        )
        assert link.entity_type == "chain_group"

    def test_unique_constraint_exists(self):
        assert any(
            c.name == "uq_scoring_config_link"
            for c in ScoringConfigLink.__table__.constraints
            if hasattr(c, "name")
        )

    def test_config_link_table_name(self):
        assert ScoringConfigLink.__tablename__ == "scoring_config_links"


class TestScoreRecalcJob:
    """NIF-132: Score recalculation job model."""

    def test_job_creation(self):
        job = ScoreRecalcJob(
            profile_id=uuid.uuid4(),
            status="pending",
            total_items=500,
            processed_items=0,
        )
        assert job.status == "pending"
        assert job.total_items == 500
        assert job.processed_items == 0

    def test_job_statuses(self):
        for status in ["pending", "running", "completed", "failed"]:
            job = ScoreRecalcJob(profile_id=uuid.uuid4(), status=status)
            assert job.status == status

    def test_job_failed_with_error(self):
        job = ScoreRecalcJob(
            profile_id=uuid.uuid4(),
            status="failed",
            error_message="Connection timeout",
            total_items=100,
            processed_items=42,
        )
        assert job.status == "failed"
        assert job.error_message == "Connection timeout"
        assert job.processed_items == 42

    def test_job_table_name(self):
        assert ScoreRecalcJob.__tablename__ == "score_recalc_jobs"


class TestScoringEngineService:
    """Test scoring engine service functions."""

    def test_classify_fit(self):
        from src.services.scoring_engine import _classify_fit
        assert _classify_fit(80) == "excellent"
        assert _classify_fit(75) == "excellent"
        assert _classify_fit(60) == "good"
        assert _classify_fit(55) == "good"
        assert _classify_fit(40) == "moderate"
        assert _classify_fit(35) == "moderate"
        assert _classify_fit(20) == "poor"
        assert _classify_fit(0) == "poor"

    def test_apply_rules_threshold(self):
        from src.services.scoring_engine import _apply_rules
        rule = ScoringRule(
            profile_id=uuid.uuid4(),
            signal_name="volume",
            rule_type="threshold",
            condition={"min": 0.5},
            points=5.0,
        )
        assert _apply_rules([rule], "volume", 0.7) == 5.0
        assert _apply_rules([rule], "volume", 0.3) == 0.0

    def test_apply_rules_range(self):
        from src.services.scoring_engine import _apply_rules
        rule = ScoringRule(
            profile_id=uuid.uuid4(),
            signal_name="density",
            rule_type="range",
            condition={"min": 0.3, "max": 0.8},
            points=3.0,
        )
        assert _apply_rules([rule], "density", 0.5) == 3.0
        assert _apply_rules([rule], "density", 0.9) == 0.0
        assert _apply_rules([rule], "density", 0.1) == 0.0

    def test_apply_rules_boolean(self):
        from src.services.scoring_engine import _apply_rules
        rule = ScoringRule(
            profile_id=uuid.uuid4(),
            signal_name="independent",
            rule_type="boolean",
            condition={"expected": True},
            points=10.0,
        )
        assert _apply_rules([rule], "independent", 1.0) == 10.0
        assert _apply_rules([rule], "independent", 0.0) == 0.0

    def test_apply_rules_wrong_signal_ignored(self):
        from src.services.scoring_engine import _apply_rules
        rule = ScoringRule(
            profile_id=uuid.uuid4(),
            signal_name="volume",
            rule_type="threshold",
            condition={"min": 0.5},
            points=5.0,
        )
        assert _apply_rules([rule], "density", 0.7) == 0.0

    def test_eval_map_has_all_signals(self):
        from src.services.scoring_engine import _EVAL_MAP
        expected = {"independent", "platform_dependency", "pos", "density", "volume", "cuisine_fit", "price_point", "engagement"}
        assert set(_EVAL_MAP.keys()) == expected


class TestScoringEngineRouter:
    """Test router registration and imports."""

    def test_router_importable(self):
        from src.api.routers.scoring_engine import router
        assert router.prefix == "/scoring-engine"

    def test_router_has_profile_endpoints(self):
        from src.api.routers.scoring_engine import router
        paths = [r.path for r in router.routes]
        assert "/scoring-engine/profiles" in paths
        assert "/scoring-engine/profiles/{profile_id}" in paths

    def test_router_has_scoring_endpoints(self):
        from src.api.routers.scoring_engine import router
        paths = [r.path for r in router.routes]
        assert "/scoring-engine/score/{restaurant_id}" in paths
        assert "/scoring-engine/explanations/{restaurant_id}" in paths

    def test_router_has_recalc_endpoints(self):
        from src.api.routers.scoring_engine import router
        paths = [r.path for r in router.routes]
        assert "/scoring-engine/recalculate" in paths
        assert "/scoring-engine/recalculate/{job_id}" in paths

    def test_router_registered_in_app(self):
        from src.main import app
        paths = [r.path for r in app.routes]
        scoring_paths = [p for p in paths if "scoring-engine" in p]
        assert len(scoring_paths) > 0
