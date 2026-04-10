"""Tests for Sales Rep Work Queue & Priority Engine (NIF-145, NIF-146, NIF-147)."""

import uuid

import pytest

from src.db.models import RepQueueItem, RepQueueRanking, Restaurant, ICPScore


# -- NIF-145: RepQueueItem model tests ----------------------------------------


class TestRepQueueItemModel:
    """NIF-145: Rep queue item model."""

    def test_item_creation(self):
        item = RepQueueItem(
            rep_id="rep-001",
            restaurant_id=uuid.uuid4(),
            priority_score=75.5,
            status="pending",
            reason="High ICP score",
            context_data={"icp_score": 85.0, "fit_label": "strong"},
        )
        assert item.rep_id == "rep-001"
        assert item.priority_score == 75.5
        assert item.status == "pending"
        assert item.reason == "High ICP score"
        assert item.context_data["icp_score"] == 85.0

    def test_item_defaults(self):
        item = RepQueueItem(
            rep_id="rep-001",
            restaurant_id=uuid.uuid4(),
        )
        assert item.status is None or item.status == "pending"
        assert item.priority_score is None or item.priority_score == 0.0
        assert item.lead_id is None

    def test_item_with_lead(self):
        lead_id = uuid.uuid4()
        item = RepQueueItem(
            rep_id="rep-001",
            restaurant_id=uuid.uuid4(),
            lead_id=lead_id,
            priority_score=60.0,
            status="pending",
        )
        assert item.lead_id == lead_id

    def test_valid_statuses(self):
        for status in ["pending", "claimed", "completed", "skipped"]:
            item = RepQueueItem(
                rep_id="rep-001",
                restaurant_id=uuid.uuid4(),
                status=status,
                priority_score=50.0,
            )
            assert item.status == status

    def test_table_name(self):
        assert RepQueueItem.__tablename__ == "rep_queue_items"

    def test_has_restaurant_id_fk(self):
        col = RepQueueItem.__table__.c.restaurant_id
        assert len(col.foreign_keys) == 1

    def test_has_lead_id_fk(self):
        col = RepQueueItem.__table__.c.lead_id
        assert len(col.foreign_keys) == 1

    def test_priority_score_indexed(self):
        col = RepQueueItem.__table__.c.priority_score
        assert col.index is True

    def test_status_indexed(self):
        col = RepQueueItem.__table__.c.status
        assert col.index is True

    def test_rep_id_indexed(self):
        col = RepQueueItem.__table__.c.rep_id
        assert col.index is True


# -- NIF-146: RepQueueRanking model tests -------------------------------------


class TestRepQueueRankingModel:
    """NIF-146: Rep queue ranking model."""

    def test_ranking_creation(self):
        ranking = RepQueueRanking(
            rep_id="rep-001",
            total_items=100,
            completed_today=15,
            avg_completion_time_mins=8.5,
            active_items=20,
            ranking_score=85.0,
        )
        assert ranking.rep_id == "rep-001"
        assert ranking.total_items == 100
        assert ranking.completed_today == 15
        assert ranking.avg_completion_time_mins == 8.5
        assert ranking.active_items == 20
        assert ranking.ranking_score == 85.0

    def test_ranking_defaults(self):
        ranking = RepQueueRanking(rep_id="rep-002")
        assert ranking.total_items is None or ranking.total_items == 0
        assert ranking.completed_today is None or ranking.completed_today == 0
        assert ranking.avg_completion_time_mins is None or ranking.avg_completion_time_mins == 0.0
        assert ranking.active_items is None or ranking.active_items == 0
        assert ranking.ranking_score is None or ranking.ranking_score == 0.0

    def test_table_name(self):
        assert RepQueueRanking.__tablename__ == "rep_queue_rankings"

    def test_rep_id_unique(self):
        col = RepQueueRanking.__table__.c.rep_id
        assert col.unique is True

    def test_ranking_score_indexed(self):
        col = RepQueueRanking.__table__.c.ranking_score
        assert col.index is True


# -- Service logic tests ------------------------------------------------------


class TestRepQueueService:
    """Test rep queue service helper functions."""

    def test_compute_priority_high_icp(self):
        from src.services.rep_queue import _compute_priority
        context = {"icp_score": 90.0, "fit_label": "strong", "engagement_recency": 0.8}
        priority = _compute_priority(context)
        assert priority > 60.0  # high ICP + strong fit + good recency

    def test_compute_priority_low_icp(self):
        from src.services.rep_queue import _compute_priority
        context = {"icp_score": 10.0, "fit_label": "weak", "engagement_recency": 0.1}
        priority = _compute_priority(context)
        assert priority < 20.0

    def test_compute_priority_no_context(self):
        from src.services.rep_queue import _compute_priority
        priority = _compute_priority(None)
        assert priority == 50.0  # default

    def test_compute_priority_empty_context(self):
        from src.services.rep_queue import _compute_priority
        # Empty dict is falsy in Python, so returns DEFAULT_PRIORITY
        priority = _compute_priority({})
        assert priority == 50.0

    def test_compute_priority_clamped_to_100(self):
        from src.services.rep_queue import _compute_priority
        context = {"icp_score": 200.0, "fit_label": "strong", "engagement_recency": 1.0}
        priority = _compute_priority(context)
        assert priority <= 100.0

    def test_compute_priority_clamped_to_0(self):
        from src.services.rep_queue import _compute_priority
        context = {"icp_score": 0.0, "fit_label": "unknown", "engagement_recency": 0.0}
        priority = _compute_priority(context)
        assert priority >= 0.0

    def test_fit_label_bonus_values(self):
        from src.services.rep_queue import FIT_LABEL_BONUS
        assert FIT_LABEL_BONUS["strong"] > FIT_LABEL_BONUS["good"]
        assert FIT_LABEL_BONUS["good"] > FIT_LABEL_BONUS["moderate"]
        assert FIT_LABEL_BONUS["moderate"] > FIT_LABEL_BONUS["weak"]
        assert FIT_LABEL_BONUS["weak"] == 0.0


# -- NIF-147: Router tests ----------------------------------------------------


class TestRepQueueRouter:
    """NIF-147: Rep queue API router."""

    def test_router_importable(self):
        from src.api.routers.rep_queue import router
        assert router.prefix == "/rep-queue"

    def test_router_has_get_queue_endpoint(self):
        from src.api.routers.rep_queue import router
        paths = [r.path for r in router.routes]
        assert "/rep-queue/{rep_id}" in paths

    def test_router_has_add_item_endpoint(self):
        from src.api.routers.rep_queue import router
        paths = [r.path for r in router.routes]
        assert "/rep-queue/items" in paths

    def test_router_has_claim_endpoint(self):
        from src.api.routers.rep_queue import router
        paths = [r.path for r in router.routes]
        assert "/rep-queue/items/{item_id}/claim" in paths

    def test_router_has_complete_endpoint(self):
        from src.api.routers.rep_queue import router
        paths = [r.path for r in router.routes]
        assert "/rep-queue/items/{item_id}/complete" in paths

    def test_router_has_skip_endpoint(self):
        from src.api.routers.rep_queue import router
        paths = [r.path for r in router.routes]
        assert "/rep-queue/items/{item_id}/skip" in paths

    def test_router_has_ranking_endpoint(self):
        from src.api.routers.rep_queue import router
        paths = [r.path for r in router.routes]
        assert "/rep-queue/{rep_id}/ranking" in paths

    def test_router_has_populate_endpoint(self):
        from src.api.routers.rep_queue import router
        paths = [r.path for r in router.routes]
        assert "/rep-queue/{rep_id}/populate" in paths

    def test_router_registered_in_app(self):
        from src.main import app
        paths = [r.path for r in app.routes]
        rep_queue_paths = [p for p in paths if "rep-queue" in p]
        assert len(rep_queue_paths) > 0

    def test_router_tags(self):
        from src.api.routers.rep_queue import router
        assert "rep-queue" in router.tags

    def test_item_to_dict_helper(self):
        from src.api.routers.rep_queue import _item_to_dict
        item = RepQueueItem(
            id=uuid.uuid4(),
            rep_id="rep-001",
            restaurant_id=uuid.uuid4(),
            priority_score=75.0,
            status="pending",
            reason="Test",
            context_data={"icp_score": 80.0},
        )
        d = _item_to_dict(item)
        assert d["rep_id"] == "rep-001"
        assert d["priority_score"] == 75.0
        assert d["status"] == "pending"
        assert d["context_data"]["icp_score"] == 80.0

    def test_ranking_to_dict_helper(self):
        from src.api.routers.rep_queue import _ranking_to_dict
        ranking = RepQueueRanking(
            id=uuid.uuid4(),
            rep_id="rep-001",
            total_items=50,
            completed_today=10,
            avg_completion_time_mins=5.5,
            active_items=15,
            ranking_score=60.0,
        )
        d = _ranking_to_dict(ranking)
        assert d["rep_id"] == "rep-001"
        assert d["total_items"] == 50
        assert d["completed_today"] == 10
        assert d["ranking_score"] == 60.0
