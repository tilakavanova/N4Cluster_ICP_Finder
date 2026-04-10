"""Tests for Outreach Orchestration & Campaign Engine (NIF-133 through NIF-136)."""

import uuid
from datetime import datetime, timezone

import pytest

from src.db.models import (
    OutreachCampaign, OutreachTarget, OutreachActivity, OutreachPerformance,
)


class TestOutreachCampaignModel:
    """NIF-133: Outreach campaign model."""

    def test_campaign_creation(self):
        campaign = OutreachCampaign(
            name="Q2 Pizza Outreach",
            campaign_type="email",
            status="draft",
            target_criteria={"min_icp_score": 60, "cuisines": ["pizza", "italian"]},
            created_by="sales_team",
        )
        assert campaign.name == "Q2 Pizza Outreach"
        assert campaign.campaign_type == "email"
        assert campaign.status == "draft"
        assert campaign.target_criteria["min_icp_score"] == 60
        assert campaign.created_by == "sales_team"

    def test_campaign_defaults(self):
        campaign = OutreachCampaign(name="Test Campaign")
        assert campaign.campaign_type is None or campaign.campaign_type == "email"
        assert campaign.status is None or campaign.status == "draft"

    def test_campaign_types(self):
        for ctype in ["email", "call", "sms", "multi"]:
            campaign = OutreachCampaign(name=f"{ctype} campaign", campaign_type=ctype)
            assert campaign.campaign_type == ctype

    def test_campaign_statuses(self):
        for status in ["draft", "active", "paused", "completed"]:
            campaign = OutreachCampaign(name="test", status=status)
            assert campaign.status == status

    def test_campaign_date_fields(self):
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        end = datetime(2026, 6, 30, tzinfo=timezone.utc)
        campaign = OutreachCampaign(
            name="Dated Campaign",
            start_date=start,
            end_date=end,
        )
        assert campaign.start_date == start
        assert campaign.end_date == end

    def test_campaign_table_name(self):
        assert OutreachCampaign.__tablename__ == "outreach_campaigns"

    def test_campaign_target_criteria_jsonb(self):
        criteria = {
            "min_icp_score": 70,
            "zip_codes": ["10001", "10002"],
            "cuisines": ["pizza"],
            "exclude_chains": True,
        }
        campaign = OutreachCampaign(name="Complex", target_criteria=criteria)
        assert campaign.target_criteria["exclude_chains"] is True
        assert len(campaign.target_criteria["zip_codes"]) == 2


class TestOutreachTargetModel:
    """NIF-134: Outreach target model."""

    def test_target_creation(self):
        campaign_id = uuid.uuid4()
        restaurant_id = uuid.uuid4()
        target = OutreachTarget(
            campaign_id=campaign_id,
            restaurant_id=restaurant_id,
            status="pending",
            priority=10,
            assigned_to="john@example.com",
        )
        assert target.campaign_id == campaign_id
        assert target.restaurant_id == restaurant_id
        assert target.status == "pending"
        assert target.priority == 10
        assert target.assigned_to == "john@example.com"

    def test_target_with_lead(self):
        lead_id = uuid.uuid4()
        target = OutreachTarget(
            campaign_id=uuid.uuid4(),
            restaurant_id=uuid.uuid4(),
            lead_id=lead_id,
        )
        assert target.lead_id == lead_id

    def test_target_without_lead(self):
        target = OutreachTarget(
            campaign_id=uuid.uuid4(),
            restaurant_id=uuid.uuid4(),
        )
        assert target.lead_id is None

    def test_target_statuses(self):
        for status in ["pending", "contacted", "responded", "converted", "skipped"]:
            target = OutreachTarget(
                campaign_id=uuid.uuid4(),
                restaurant_id=uuid.uuid4(),
                status=status,
            )
            assert target.status == status

    def test_target_defaults(self):
        target = OutreachTarget(
            campaign_id=uuid.uuid4(),
            restaurant_id=uuid.uuid4(),
        )
        assert target.priority is None or target.priority == 0

    def test_target_table_name(self):
        assert OutreachTarget.__tablename__ == "outreach_targets"


class TestOutreachActivityModel:
    """NIF-135: Outreach activity model."""

    def test_activity_creation(self):
        target_id = uuid.uuid4()
        activity = OutreachActivity(
            target_id=target_id,
            activity_type="email_sent",
            outcome="interested",
            notes="Responded positively to demo offer",
            performed_by="jane@example.com",
        )
        assert activity.target_id == target_id
        assert activity.activity_type == "email_sent"
        assert activity.outcome == "interested"
        assert "demo offer" in activity.notes
        assert activity.performed_by == "jane@example.com"

    def test_activity_types(self):
        for atype in ["email_sent", "call_made", "sms_sent", "meeting", "note"]:
            activity = OutreachActivity(
                target_id=uuid.uuid4(),
                activity_type=atype,
            )
            assert activity.activity_type == atype

    def test_activity_outcomes(self):
        for outcome in ["no_answer", "interested", "not_interested", "callback", "converted"]:
            activity = OutreachActivity(
                target_id=uuid.uuid4(),
                activity_type="call_made",
                outcome=outcome,
            )
            assert activity.outcome == outcome

    def test_activity_no_outcome(self):
        activity = OutreachActivity(
            target_id=uuid.uuid4(),
            activity_type="note",
        )
        assert activity.outcome is None

    def test_activity_table_name(self):
        assert OutreachActivity.__tablename__ == "outreach_activities"


class TestOutreachPerformanceModel:
    """NIF-136: Outreach performance summary model."""

    def test_performance_creation(self):
        campaign_id = uuid.uuid4()
        perf = OutreachPerformance(
            campaign_id=campaign_id,
            total_targets=100,
            contacted=75,
            responded=30,
            converted=10,
            response_rate=40.0,
            conversion_rate=10.0,
        )
        assert perf.campaign_id == campaign_id
        assert perf.total_targets == 100
        assert perf.contacted == 75
        assert perf.responded == 30
        assert perf.converted == 10
        assert perf.response_rate == 40.0
        assert perf.conversion_rate == 10.0

    def test_performance_defaults(self):
        perf = OutreachPerformance(campaign_id=uuid.uuid4())
        assert perf.total_targets is None or perf.total_targets == 0
        assert perf.contacted is None or perf.contacted == 0
        assert perf.response_rate is None or perf.response_rate == 0.0

    def test_performance_unique_campaign(self):
        """Verify unique constraint on campaign_id exists."""
        from sqlalchemy import inspect
        mapper = inspect(OutreachPerformance)
        campaign_col = mapper.columns["campaign_id"]
        assert campaign_col.unique is True

    def test_performance_table_name(self):
        assert OutreachPerformance.__tablename__ == "outreach_performance"

    def test_performance_rates_calculation(self):
        """Test that rates are stored as percentages."""
        perf = OutreachPerformance(
            campaign_id=uuid.uuid4(),
            total_targets=200,
            contacted=150,
            responded=60,
            converted=20,
            response_rate=40.0,   # 60/150 * 100
            conversion_rate=10.0,  # 20/200 * 100
        )
        assert perf.response_rate == 40.0
        assert perf.conversion_rate == 10.0


class TestOutreachService:
    """Test outreach service constants and validation."""

    def test_valid_campaign_types(self):
        from src.services.outreach import VALID_CAMPAIGN_TYPES
        assert VALID_CAMPAIGN_TYPES == {"email", "call", "sms", "multi"}

    def test_valid_campaign_statuses(self):
        from src.services.outreach import VALID_CAMPAIGN_STATUSES
        assert VALID_CAMPAIGN_STATUSES == {"draft", "active", "paused", "completed"}

    def test_valid_target_statuses(self):
        from src.services.outreach import VALID_TARGET_STATUSES
        assert VALID_TARGET_STATUSES == {"pending", "contacted", "responded", "converted", "skipped"}

    def test_valid_activity_types(self):
        from src.services.outreach import VALID_ACTIVITY_TYPES
        assert VALID_ACTIVITY_TYPES == {"email_sent", "call_made", "sms_sent", "meeting", "note"}

    def test_valid_outcomes(self):
        from src.services.outreach import VALID_OUTCOMES
        assert VALID_OUTCOMES == {"no_answer", "interested", "not_interested", "callback", "converted"}


class TestOutreachRouter:
    """Test router registration and endpoints."""

    def test_router_importable(self):
        from src.api.routers.outreach import router
        assert router.prefix == "/outreach"

    def test_router_has_campaign_endpoints(self):
        from src.api.routers.outreach import router
        paths = [r.path for r in router.routes]
        assert "/outreach/campaigns" in paths
        assert "/outreach/campaigns/{campaign_id}" in paths

    def test_router_has_target_endpoints(self):
        from src.api.routers.outreach import router
        paths = [r.path for r in router.routes]
        assert "/outreach/campaigns/{campaign_id}/targets" in paths
        assert "/outreach/campaigns/{campaign_id}/targets/select" in paths

    def test_router_has_activity_endpoints(self):
        from src.api.routers.outreach import router
        paths = [r.path for r in router.routes]
        assert "/outreach/targets/{target_id}/activities" in paths

    def test_router_has_performance_endpoints(self):
        from src.api.routers.outreach import router
        paths = [r.path for r in router.routes]
        assert "/outreach/campaigns/{campaign_id}/performance" in paths

    def test_router_has_target_status_endpoint(self):
        from src.api.routers.outreach import router
        paths = [r.path for r in router.routes]
        assert "/outreach/targets/{target_id}/status" in paths

    def test_router_registered_in_app(self):
        from src.main import app
        paths = [r.path for r in app.routes]
        outreach_paths = [p for p in paths if "outreach" in p]
        assert len(outreach_paths) > 0

    def test_campaign_crud_methods(self):
        from src.api.routers.outreach import router
        methods_by_path = {}
        for route in router.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set())
            if path not in methods_by_path:
                methods_by_path[path] = set()
            methods_by_path[path].update(methods)

        assert "POST" in methods_by_path.get("/outreach/campaigns", set())
        assert "GET" in methods_by_path.get("/outreach/campaigns", set())
        assert "DELETE" in methods_by_path.get("/outreach/campaigns/{campaign_id}", set())
        assert "PATCH" in methods_by_path.get("/outreach/campaigns/{campaign_id}", set())


class TestOutreachSchemas:
    """Test Pydantic schemas for validation."""

    def test_campaign_create_schema(self):
        from src.api.routers.outreach import CampaignCreate
        data = CampaignCreate(
            name="Test",
            campaign_type="email",
            target_criteria={"min_score": 50},
        )
        assert data.name == "Test"
        assert data.campaign_type == "email"

    def test_campaign_create_invalid_type(self):
        from src.api.routers.outreach import CampaignCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CampaignCreate(name="Test", campaign_type="invalid_type")

    def test_campaign_update_schema(self):
        from src.api.routers.outreach import CampaignUpdate
        data = CampaignUpdate(status="active")
        assert data.status == "active"
        assert data.name is None

    def test_campaign_update_invalid_status(self):
        from src.api.routers.outreach import CampaignUpdate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CampaignUpdate(status="invalid_status")

    def test_target_add_schema(self):
        from src.api.routers.outreach import TargetAdd
        rid = uuid.uuid4()
        data = TargetAdd(restaurant_id=rid, priority=5)
        assert data.restaurant_id == rid
        assert data.priority == 5
        assert data.lead_id is None

    def test_target_select_schema(self):
        from src.api.routers.outreach import TargetSelect
        data = TargetSelect(
            min_icp_score=60.0,
            zip_codes=["10001", "10002"],
            cuisines=["pizza"],
            limit=100,
        )
        assert data.min_icp_score == 60.0
        assert len(data.zip_codes) == 2

    def test_target_select_limit_bounds(self):
        from src.api.routers.outreach import TargetSelect
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TargetSelect(limit=0)  # below min
        with pytest.raises(ValidationError):
            TargetSelect(limit=1001)  # above max

    def test_activity_create_schema(self):
        from src.api.routers.outreach import ActivityCreate
        data = ActivityCreate(
            activity_type="call_made",
            outcome="interested",
            notes="Great conversation",
            performed_by="agent_1",
        )
        assert data.activity_type == "call_made"
        assert data.outcome == "interested"

    def test_activity_create_invalid_type(self):
        from src.api.routers.outreach import ActivityCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ActivityCreate(activity_type="invalid_type")

    def test_activity_create_invalid_outcome(self):
        from src.api.routers.outreach import ActivityCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ActivityCreate(activity_type="email_sent", outcome="invalid_outcome")
