"""Integration tests: verify all templates render without errors using real Jinja2 engine.

These tests catch variable mismatches (UndefinedError) by actually rendering
each template with the exact variables the routes would pass in empty-state scenarios.
"""

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape, UndefinedError


@pytest.fixture(scope="module")
def jinja_env():
    """Create strict Jinja2 environment that raises on undefined variables."""
    return Environment(
        loader=FileSystemLoader("src/dashboard/templates"),
        autoescape=select_autoescape(["html"]),
        undefined=__import__("jinja2").StrictUndefined,
    )


class TestNeighborhoodsRendering:
    def test_renders_empty_state(self, jinja_env):
        t = jinja_env.get_template("neighborhoods.html")
        html = t.render(
            neighborhoods=[],
            filters={"state": "", "city": "", "min_restaurants": 3},
            page=1,
            total_pages=1,
            message="",
            active_tab="neighborhoods",
        )
        assert "Neighborhoods" in html or "neighborhoods" in html

    def test_renders_with_data(self, jinja_env):
        t = jinja_env.get_template("neighborhoods.html")
        html = t.render(
            neighborhoods=[
                {"rank": 1, "zip_code": "10001", "name": "Midtown, NY", "city": "New York", "state": "NY",
                 "restaurant_count": 42, "avg_icp_score": 68.5, "opportunity_score": 78.3,
                 "independent_ratio": 0.72, "delivery_coverage": 0.85, "top_cuisines": ["Italian", "Pizza"]},
            ],
            filters={"state": "NY", "city": "", "min_restaurants": 3},
            page=1,
            total_pages=1,
            message="",
            active_tab="neighborhoods",
        )
        assert "10001" in html
        assert "Midtown" in html


class TestOutreachRendering:
    def test_renders_empty_state(self, jinja_env):
        t = jinja_env.get_template("outreach.html")
        html = t.render(
            campaigns=[],
            campaign_stats=[],
            message="",
            active_tab="outreach",
        )
        assert "outreach" in html.lower() or "campaign" in html.lower()


class TestQueueRendering:
    def test_renders_empty_state(self, jinja_env):
        t = jinja_env.get_template("queue.html")
        html = t.render(
            items=[],
            ranking=None,
            rep_id="",
            message="",
            active_tab="queue",
        )
        assert "queue" in html.lower() or "Queue" in html

    def test_renders_with_rep_id(self, jinja_env):
        t = jinja_env.get_template("queue.html")
        html = t.render(
            items=[],
            ranking=None,
            rep_id="sales_rep_1",
            message="",
            active_tab="queue",
        )
        assert "sales_rep_1" in html


class TestQualificationRendering:
    def test_renders_empty_state(self, jinja_env):
        t = jinja_env.get_template("qualification.html")
        html = t.render(
            pending=[],
            stats={"qualified": 0, "not_qualified": 0, "needs_review": 0},
            message="",
            active_tab="qualification",
        )
        assert "qualification" in html.lower() or "Qualification" in html

    def test_renders_with_stats(self, jinja_env):
        t = jinja_env.get_template("qualification.html")
        html = t.render(
            pending=[],
            stats={"qualified": 10, "not_qualified": 5, "needs_review": 3},
            message="",
            active_tab="qualification",
        )
        assert "10" in html or "qualified" in html.lower()


class TestClustersRendering:
    def test_renders_empty_state(self, jinja_env):
        t = jinja_env.get_template("clusters.html")
        html = t.render(
            clusters=[],
            stats={"total_clusters": 0, "total_members": 0, "avg_flywheel": None},
            message="",
            active_tab="clusters",
        )
        assert "cluster" in html.lower()


class TestAnalyticsRendering:
    def test_renders_with_new_sections(self, jinja_env):
        t = jinja_env.get_template("analytics.html")
        html = t.render(
            overview={"total_leads": 0, "total_restaurants": 0, "total_jobs": 0, "conversion_rate": 0},
            by_source=[],
            by_fit=[],
            by_status=[],
            over_time=[],
            top_cities=[],
            funnel=None,
            top_neighborhoods=[],
            cluster_stats={},
            active_tab="analytics",
        )
        assert "analytics" in html.lower() or "Analytics" in html


class TestLeadDetailRendering:
    def test_renders_with_tasks_and_history(self, jinja_env):
        """Test that lead detail template renders with tasks and history variables."""
        from unittest.mock import MagicMock
        from datetime import datetime, timezone

        lead = MagicMock()
        lead.id = "test-id"
        lead.first_name = "John"
        lead.last_name = "Doe"
        lead.email = "john@example.com"
        lead.company = "Test Corp"
        lead.business_type = "Restaurant"
        lead.locations = "1"
        lead.interest = "Demo"
        lead.source = "website_demo"
        lead.status = "new"
        lead.message = None
        lead.utm_source = None
        lead.utm_medium = None
        lead.utm_campaign = None
        lead.icp_fit_label = None
        lead.icp_total_score = None
        lead.is_independent = None
        lead.has_delivery = None
        lead.has_pos = None
        lead.geo_density_score = None
        lead.delivery_platforms = []
        lead.matched_restaurant_name = None
        lead.match_confidence = None
        lead.pos_provider = None
        lead.hubspot_contact_id = None
        lead.hubspot_deal_id = None
        lead.created_at = datetime.now(timezone.utc)

        t = jinja_env.get_template("lead_detail.html")
        html = t.render(
            lead=lead,
            audit_logs=[],
            tasks=[],
            stage_history=[],
            assignment_history=[],
            account=None,
            contact=None,
            active_tab="leads",
        )
        assert "John" in html
        assert "Follow-up Tasks" in html
        assert "Lifecycle History" in html


class TestPartialTemplates:
    def test_neighborhoods_compare_exists_and_renders(self, jinja_env):
        t = jinja_env.get_template("neighborhoods_compare.html")
        html = t.render(comparison={"neighborhoods": [], "winners": {}})
        assert html is not None

    def test_cluster_detail_exists_and_renders(self, jinja_env):
        t = jinja_env.get_template("cluster_detail.html")
        html = t.render(cluster={"id": "test", "name": "Test Cluster", "members": [], "expansion_plans": []}, history=[])
        assert html is not None

    def test_campaign_detail_exists_and_renders(self, jinja_env):
        t = jinja_env.get_template("campaign_detail.html")
        html = t.render(campaign={"id": "test", "name": "Test Campaign", "status": "draft"}, targets=[], performance=None)
        assert html is not None
