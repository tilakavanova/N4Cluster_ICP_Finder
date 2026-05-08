"""Tests for Rep Queue Next-Best-Action (NIF-242)."""

import pytest

from src.services.rep_queue import (
    _determine_action,
    INTENT_ACTION_MAP,
)


class TestIntentActionMap:
    """NIF-242: Intent-to-action mapping."""

    def test_high_intent_maps_to_schedule_demo(self):
        assert INTENT_ACTION_MAP["high_intent"] == "Schedule demo"

    def test_evaluating_maps_to_send_case_study(self):
        assert INTENT_ACTION_MAP["evaluating"] == "Send case study"

    def test_researching_maps_to_follow_up(self):
        assert INTENT_ACTION_MAP["researching"] == "Follow up email"

    def test_cold_maps_to_reengage(self):
        assert INTENT_ACTION_MAP["cold"] == "Re-engage sequence"

    def test_unknown_maps_to_reengage(self):
        assert INTENT_ACTION_MAP["unknown"] == "Re-engage sequence"

    def test_aware_maps_to_follow_up(self):
        assert INTENT_ACTION_MAP["aware"] == "Follow up email"


class TestDetermineAction:
    """NIF-242: Action determination logic."""

    def test_high_intent_basic(self):
        result = _determine_action("high_intent", "email", 5.0, 60.0, "good")
        assert result["recommended_action"] == "Schedule demo"
        assert result["intent_label"] == "high_intent"

    def test_evaluating_basic(self):
        result = _determine_action("evaluating", "email", 5.0, 50.0, "moderate")
        assert result["recommended_action"] == "Send case study"

    def test_researching_basic(self):
        result = _determine_action("researching", "email", 10.0, 40.0, "moderate")
        assert result["recommended_action"] == "Follow up email"

    def test_cold_basic(self):
        result = _determine_action("cold", None, None, 30.0, "weak")
        assert result["recommended_action"] == "Re-engage sequence"

    def test_cold_high_value_override(self):
        """High ICP + strong fit + cold should escalate to priority re-engage."""
        result = _determine_action("cold", "email", 40.0, 80.0, "strong")
        assert "Priority re-engage" in result["recommended_action"]

    def test_cold_good_fit_high_icp_override(self):
        result = _determine_action("cold", "email", 40.0, 75.0, "good")
        assert "Priority re-engage" in result["recommended_action"]

    def test_cold_low_icp_no_override(self):
        """Cold + low ICP should NOT escalate."""
        result = _determine_action("cold", "email", 40.0, 50.0, "strong")
        assert result["recommended_action"] == "Re-engage sequence"

    def test_evaluating_recently_contacted_override(self):
        """Evaluating + contacted < 3 days ago should wait."""
        result = _determine_action("evaluating", "email", 1.5, 60.0, "good")
        assert "Wait" in result["recommended_action"]

    def test_evaluating_not_recently_contacted(self):
        """Evaluating + contacted > 3 days ago should send case study."""
        result = _determine_action("evaluating", "email", 5.0, 60.0, "good")
        assert result["recommended_action"] == "Send case study"

    def test_result_includes_all_fields(self):
        result = _determine_action("high_intent", "meeting", 2.0, 85.0, "strong")
        assert "recommended_action" in result
        assert "intent_label" in result
        assert "last_activity_type" in result
        assert "days_since_contact" in result
        assert "icp_score" in result
        assert "fit_label" in result

    def test_none_days_since_contact(self):
        result = _determine_action("cold", None, None, 40.0, "moderate")
        assert result["days_since_contact"] is None

    def test_days_since_contact_rounded(self):
        result = _determine_action("high_intent", "email", 3.456, 60.0, "good")
        assert result["days_since_contact"] == 3.5

    def test_unknown_intent_falls_back(self):
        result = _determine_action("unknown", None, None, 20.0, "unknown")
        assert result["recommended_action"] == "Re-engage sequence"


class TestNextBestActionImports:
    """NIF-242: Verify functions are importable and exist."""

    def test_get_next_best_action_importable(self):
        from src.services.rep_queue import get_next_best_action
        assert callable(get_next_best_action)

    def test_enrich_queue_with_actions_importable(self):
        from src.services.rep_queue import enrich_queue_with_actions
        assert callable(enrich_queue_with_actions)

    def test_fetch_engagement_data_importable(self):
        from src.services.rep_queue import _fetch_engagement_data
        assert callable(_fetch_engagement_data)


class TestNextActionRouterEndpoints:
    """NIF-242: Verify next-action endpoints are registered."""

    def test_next_action_endpoint_exists(self):
        from src.api.routers.rep_queue import router
        paths = [r.path for r in router.routes]
        assert "/rep-queue/{rep_id}/next-action" in paths

    def test_enrich_actions_endpoint_exists(self):
        from src.api.routers.rep_queue import router
        paths = [r.path for r in router.routes]
        assert "/rep-queue/{rep_id}/enrich-actions" in paths

    def test_registered_in_main_app(self):
        from src.main import app
        paths = [r.path for r in app.routes]
        next_action_paths = [p for p in paths if "next-action" in p]
        assert len(next_action_paths) > 0
