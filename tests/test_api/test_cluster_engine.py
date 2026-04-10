"""Tests for Cluster Expansion Engine (NIF-151 through NIF-159)."""

import uuid

import pytest

from src.db.models import (
    MerchantCluster, ClusterMember, ClusterExpansionPlan,
    ClusterHistory, ClusterFeedback,
)


# -- NIF-151: MerchantCluster model tests -------------------------------------


class TestMerchantClusterModel:
    """NIF-151: MerchantCluster model."""

    def test_cluster_creation(self):
        cluster = MerchantCluster(
            name="Test Cluster",
            cluster_type="geographic",
            zip_codes=["10001", "10002"],
            center_lat=40.7128,
            center_lng=-74.0060,
            radius_miles=1.5,
            restaurant_count=8,
            avg_icp_score=72.5,
            flywheel_score=55.0,
            status="detected",
            detection_params={"min_size": 3},
        )
        assert cluster.name == "Test Cluster"
        assert cluster.cluster_type == "geographic"
        assert cluster.zip_codes == ["10001", "10002"]
        assert cluster.center_lat == 40.7128
        assert cluster.center_lng == -74.0060
        assert cluster.radius_miles == 1.5
        assert cluster.restaurant_count == 8
        assert cluster.avg_icp_score == 72.5
        assert cluster.flywheel_score == 55.0
        assert cluster.status == "detected"

    def test_cluster_defaults(self):
        cluster = MerchantCluster(name="Default Cluster")
        assert cluster.cluster_type is None or cluster.cluster_type == "geographic"
        assert cluster.restaurant_count is None or cluster.restaurant_count == 0
        assert cluster.avg_icp_score is None or cluster.avg_icp_score == 0.0
        assert cluster.flywheel_score is None or cluster.flywheel_score == 0.0

    def test_table_name(self):
        assert MerchantCluster.__tablename__ == "merchant_clusters"

    def test_valid_statuses(self):
        for status in ["detected", "active", "expanding", "mature"]:
            cluster = MerchantCluster(name="Test", status=status)
            assert cluster.status == status

    def test_valid_cluster_types(self):
        for ctype in ["geographic", "cuisine", "chain"]:
            cluster = MerchantCluster(name="Test", cluster_type=ctype)
            assert cluster.cluster_type == ctype

    def test_name_indexed(self):
        col = MerchantCluster.__table__.c.name
        assert col.index is True

    def test_status_indexed(self):
        col = MerchantCluster.__table__.c.status
        assert col.index is True

    def test_detection_params_jsonb(self):
        cluster = MerchantCluster(
            name="Test",
            detection_params={"zip_code": "10001", "min_size": 5},
        )
        assert cluster.detection_params["zip_code"] == "10001"
        assert cluster.detection_params["min_size"] == 5


# -- NIF-152: ClusterMember model tests ----------------------------------------


class TestClusterMemberModel:
    """NIF-152: ClusterMember model."""

    def test_member_creation(self):
        cluster_id = uuid.uuid4()
        restaurant_id = uuid.uuid4()
        member = ClusterMember(
            cluster_id=cluster_id,
            restaurant_id=restaurant_id,
            role="anchor",
            icp_score_at_join=85.0,
        )
        assert member.cluster_id == cluster_id
        assert member.restaurant_id == restaurant_id
        assert member.role == "anchor"
        assert member.icp_score_at_join == 85.0

    def test_member_defaults(self):
        member = ClusterMember(
            cluster_id=uuid.uuid4(),
            restaurant_id=uuid.uuid4(),
        )
        assert member.role is None or member.role == "member"
        assert member.icp_score_at_join is None or member.icp_score_at_join == 0.0

    def test_table_name(self):
        assert ClusterMember.__tablename__ == "cluster_members"

    def test_valid_roles(self):
        for role in ["anchor", "member", "prospect"]:
            member = ClusterMember(
                cluster_id=uuid.uuid4(),
                restaurant_id=uuid.uuid4(),
                role=role,
            )
            assert member.role == role

    def test_has_cluster_id_fk(self):
        col = ClusterMember.__table__.c.cluster_id
        assert len(col.foreign_keys) == 1

    def test_has_restaurant_id_fk(self):
        col = ClusterMember.__table__.c.restaurant_id
        assert len(col.foreign_keys) == 1

    def test_cluster_id_indexed(self):
        col = ClusterMember.__table__.c.cluster_id
        assert col.index is True

    def test_restaurant_id_indexed(self):
        col = ClusterMember.__table__.c.restaurant_id
        assert col.index is True

    def test_unique_constraint(self):
        constraints = [c.name for c in ClusterMember.__table__.constraints if hasattr(c, 'name')]
        assert "uq_cluster_member" in constraints


# -- NIF-153: ClusterExpansionPlan model tests ---------------------------------


class TestClusterExpansionPlanModel:
    """NIF-153: ClusterExpansionPlan model."""

    def test_plan_creation(self):
        plan = ClusterExpansionPlan(
            cluster_id=uuid.uuid4(),
            target_restaurant_id=uuid.uuid4(),
            sequence_order=1,
            strategy="Proximity outreach",
            priority_score=78.5,
            status="planned",
            notes="High-ICP target",
        )
        assert plan.sequence_order == 1
        assert plan.strategy == "Proximity outreach"
        assert plan.priority_score == 78.5
        assert plan.status == "planned"
        assert plan.notes == "High-ICP target"

    def test_plan_defaults(self):
        plan = ClusterExpansionPlan(
            cluster_id=uuid.uuid4(),
            target_restaurant_id=uuid.uuid4(),
        )
        assert plan.sequence_order is None or plan.sequence_order == 0
        assert plan.priority_score is None or plan.priority_score == 0.0

    def test_table_name(self):
        assert ClusterExpansionPlan.__tablename__ == "cluster_expansion_plans"

    def test_valid_statuses(self):
        for status in ["planned", "in_progress", "completed", "skipped"]:
            plan = ClusterExpansionPlan(
                cluster_id=uuid.uuid4(),
                target_restaurant_id=uuid.uuid4(),
                status=status,
            )
            assert plan.status == status

    def test_has_cluster_id_fk(self):
        col = ClusterExpansionPlan.__table__.c.cluster_id
        assert len(col.foreign_keys) == 1

    def test_has_target_restaurant_id_fk(self):
        col = ClusterExpansionPlan.__table__.c.target_restaurant_id
        assert len(col.foreign_keys) == 1

    def test_priority_score_indexed(self):
        col = ClusterExpansionPlan.__table__.c.priority_score
        assert col.index is True

    def test_status_indexed(self):
        col = ClusterExpansionPlan.__table__.c.status
        assert col.index is True


# -- NIF-158: ClusterHistory model tests ---------------------------------------


class TestClusterHistoryModel:
    """NIF-158: ClusterHistory model."""

    def test_history_creation(self):
        event = ClusterHistory(
            cluster_id=uuid.uuid4(),
            event_type="detected",
            details={"restaurant_count": 5},
        )
        assert event.event_type == "detected"
        assert event.details["restaurant_count"] == 5

    def test_valid_event_types(self):
        for event_type in ["detected", "member_added", "member_removed", "recalculated", "expanded", "campaign_launched"]:
            event = ClusterHistory(
                cluster_id=uuid.uuid4(),
                event_type=event_type,
            )
            assert event.event_type == event_type

    def test_table_name(self):
        assert ClusterHistory.__tablename__ == "cluster_history"

    def test_has_cluster_id_fk(self):
        col = ClusterHistory.__table__.c.cluster_id
        assert len(col.foreign_keys) == 1

    def test_event_type_indexed(self):
        col = ClusterHistory.__table__.c.event_type
        assert col.index is True


# -- NIF-159: ClusterFeedback model tests --------------------------------------


class TestClusterFeedbackModel:
    """NIF-159: ClusterFeedback model."""

    def test_feedback_creation(self):
        fb = ClusterFeedback(
            cluster_id=uuid.uuid4(),
            feedback_type="expansion_success",
            details={"merchant": "Pizza Place", "result": "signed"},
            submitted_by="rep-001",
        )
        assert fb.feedback_type == "expansion_success"
        assert fb.details["merchant"] == "Pizza Place"
        assert fb.submitted_by == "rep-001"

    def test_feedback_defaults(self):
        fb = ClusterFeedback(
            cluster_id=uuid.uuid4(),
            feedback_type="quality_rating",
        )
        assert fb.submitted_by is None or fb.submitted_by == "system"

    def test_valid_feedback_types(self):
        for fb_type in ["expansion_success", "expansion_failure", "quality_rating"]:
            fb = ClusterFeedback(
                cluster_id=uuid.uuid4(),
                feedback_type=fb_type,
            )
            assert fb.feedback_type == fb_type

    def test_table_name(self):
        assert ClusterFeedback.__tablename__ == "cluster_feedback"

    def test_has_cluster_id_fk(self):
        col = ClusterFeedback.__table__.c.cluster_id
        assert len(col.foreign_keys) == 1

    def test_feedback_type_indexed(self):
        col = ClusterFeedback.__table__.c.feedback_type
        assert col.index is True


# -- Service logic tests -------------------------------------------------------


class TestClusterEngineService:
    """Test cluster engine service constants and helpers."""

    def test_flywheel_weights_sum_to_1(self):
        from src.services.cluster_engine import FLYWHEEL_WEIGHTS
        total = sum(FLYWHEEL_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_flywheel_weights_keys(self):
        from src.services.cluster_engine import FLYWHEEL_WEIGHTS
        expected_keys = {"density", "avg_icp", "anchor_strength", "expansion_potential", "feedback_score"}
        assert set(FLYWHEEL_WEIGHTS.keys()) == expected_keys

    def test_km_per_mile_constant(self):
        from src.services.cluster_engine import KM_PER_MILE
        assert abs(KM_PER_MILE - 1.60934) < 0.001

    def test_service_functions_importable(self):
        from src.services.cluster_engine import (
            detect_clusters,
            identify_anchors,
            plan_expansion,
            estimate_flywheel,
            get_recommendations,
            launch_campaign,
            recalculate_cluster,
            record_feedback,
            get_cluster_history,
            list_clusters,
            get_cluster_detail,
        )
        assert callable(detect_clusters)
        assert callable(identify_anchors)
        assert callable(plan_expansion)
        assert callable(estimate_flywheel)
        assert callable(get_recommendations)
        assert callable(launch_campaign)
        assert callable(recalculate_cluster)
        assert callable(record_feedback)
        assert callable(get_cluster_history)
        assert callable(list_clusters)
        assert callable(get_cluster_detail)


# -- Router tests --------------------------------------------------------------


class TestClusterEngineRouter:
    """Test cluster engine API router."""

    def test_router_importable(self):
        from src.api.routers.cluster_engine import router
        assert router.prefix == "/clusters"

    def test_router_tags(self):
        from src.api.routers.cluster_engine import router
        assert "clusters" in router.tags

    def test_router_has_detect_endpoint(self):
        from src.api.routers.cluster_engine import router
        paths = [r.path for r in router.routes]
        assert "/clusters/detect" in paths

    def test_router_has_list_endpoint(self):
        from src.api.routers.cluster_engine import router
        paths = [r.path for r in router.routes]
        assert "/clusters" in paths

    def test_router_has_detail_endpoint(self):
        from src.api.routers.cluster_engine import router
        paths = [r.path for r in router.routes]
        assert "/clusters/{cluster_id}" in paths

    def test_router_has_expansion_plan_endpoint(self):
        from src.api.routers.cluster_engine import router
        paths = [r.path for r in router.routes]
        assert "/clusters/{cluster_id}/expansion-plan" in paths

    def test_router_has_recommendations_endpoint(self):
        from src.api.routers.cluster_engine import router
        paths = [r.path for r in router.routes]
        assert "/clusters/{cluster_id}/recommendations" in paths

    def test_router_has_launch_campaign_endpoint(self):
        from src.api.routers.cluster_engine import router
        paths = [r.path for r in router.routes]
        assert "/clusters/{cluster_id}/launch-campaign" in paths

    def test_router_has_recalculate_endpoint(self):
        from src.api.routers.cluster_engine import router
        paths = [r.path for r in router.routes]
        assert "/clusters/{cluster_id}/recalculate" in paths

    def test_router_has_feedback_endpoint(self):
        from src.api.routers.cluster_engine import router
        paths = [r.path for r in router.routes]
        assert "/clusters/{cluster_id}/feedback" in paths

    def test_router_has_history_endpoint(self):
        from src.api.routers.cluster_engine import router
        paths = [r.path for r in router.routes]
        assert "/clusters/{cluster_id}/history" in paths

    def test_router_registered_in_app(self):
        from src.main import app
        paths = [r.path for r in app.routes]
        cluster_paths = [p for p in paths if "clusters" in p]
        assert len(cluster_paths) > 0

    def test_cluster_to_dict_helper(self):
        from src.api.routers.cluster_engine import _cluster_to_dict
        cluster = MerchantCluster(
            id=uuid.uuid4(),
            name="Test Cluster",
            cluster_type="geographic",
            zip_codes=["10001"],
            center_lat=40.71,
            center_lng=-74.00,
            radius_miles=1.0,
            restaurant_count=5,
            avg_icp_score=70.0,
            flywheel_score=50.0,
            status="detected",
            detection_params={"min_size": 3},
        )
        d = _cluster_to_dict(cluster)
        assert d["name"] == "Test Cluster"
        assert d["cluster_type"] == "geographic"
        assert d["zip_codes"] == ["10001"]
        assert d["restaurant_count"] == 5
        assert d["avg_icp_score"] == 70.0
        assert d["flywheel_score"] == 50.0
        assert d["status"] == "detected"

    def test_member_to_dict_helper(self):
        from src.api.routers.cluster_engine import _member_to_dict
        member = ClusterMember(
            id=uuid.uuid4(),
            cluster_id=uuid.uuid4(),
            restaurant_id=uuid.uuid4(),
            role="anchor",
            icp_score_at_join=85.0,
        )
        d = _member_to_dict(member)
        assert d["role"] == "anchor"
        assert d["icp_score_at_join"] == 85.0

    def test_plan_to_dict_helper(self):
        from src.api.routers.cluster_engine import _plan_to_dict
        plan = ClusterExpansionPlan(
            id=uuid.uuid4(),
            cluster_id=uuid.uuid4(),
            target_restaurant_id=uuid.uuid4(),
            sequence_order=1,
            strategy="Proximity",
            priority_score=75.0,
            status="planned",
            notes="Test",
        )
        d = _plan_to_dict(plan)
        assert d["sequence_order"] == 1
        assert d["strategy"] == "Proximity"
        assert d["priority_score"] == 75.0
        assert d["status"] == "planned"

    def test_history_to_dict_helper(self):
        from src.api.routers.cluster_engine import _history_to_dict
        event = ClusterHistory(
            id=uuid.uuid4(),
            cluster_id=uuid.uuid4(),
            event_type="detected",
            details={"count": 5},
        )
        d = _history_to_dict(event)
        assert d["event_type"] == "detected"
        assert d["details"]["count"] == 5

    def test_feedback_to_dict_helper(self):
        from src.api.routers.cluster_engine import _feedback_to_dict
        fb = ClusterFeedback(
            id=uuid.uuid4(),
            cluster_id=uuid.uuid4(),
            feedback_type="expansion_success",
            details={"result": "signed"},
            submitted_by="rep-001",
        )
        d = _feedback_to_dict(fb)
        assert d["feedback_type"] == "expansion_success"
        assert d["submitted_by"] == "rep-001"
        assert d["details"]["result"] == "signed"
