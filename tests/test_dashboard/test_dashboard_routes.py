"""Comprehensive tests for dashboard routes (NIF-201 — all 7 stories).

Tests route registration, template existence, import paths, and route configurations.
"""

import os
import pytest

from src.dashboard.routes import router, _require_login, templates


class TestRouteRegistration:
    """Verify all 44 dashboard routes are registered."""

    def _route_paths(self):
        return [r.path for r in router.routes if hasattr(r, "path")]

    def _route_methods(self):
        return {r.path: r.methods for r in router.routes if hasattr(r, "methods")}

    # -- Existing routes --

    def test_login_routes(self):
        paths = self._route_paths()
        assert "/dashboard/login" in paths

    def test_leads_dashboard(self):
        paths = self._route_paths()
        assert "/dashboard" in paths

    def test_lead_detail(self):
        paths = self._route_paths()
        assert "/dashboard/leads/{lead_id}" in paths

    def test_jobs_dashboard(self):
        paths = self._route_paths()
        assert "/dashboard/jobs" in paths

    def test_restaurants_dashboard(self):
        paths = self._route_paths()
        assert "/dashboard/restaurants" in paths

    def test_prospects_dashboard(self):
        paths = self._route_paths()
        assert "/dashboard/prospects" in paths

    def test_analytics_dashboard(self):
        paths = self._route_paths()
        assert "/dashboard/analytics" in paths

    # -- NIF-202: Neighborhoods --

    def test_neighborhoods_get(self):
        paths = self._route_paths()
        assert "/dashboard/neighborhoods" in paths

    def test_neighborhoods_refresh(self):
        paths = self._route_paths()
        assert "/dashboard/neighborhoods/refresh-all" in paths

    def test_neighborhoods_compare(self):
        paths = self._route_paths()
        assert "/dashboard/neighborhoods/compare" in paths

    # -- NIF-203: Outreach --

    def test_outreach_list(self):
        paths = self._route_paths()
        assert "/dashboard/outreach" in paths

    def test_outreach_create_campaign(self):
        paths = self._route_paths()
        assert "/dashboard/outreach/campaigns" in paths

    def test_outreach_campaign_detail(self):
        paths = self._route_paths()
        assert "/dashboard/outreach/campaigns/{campaign_id}" in paths

    def test_outreach_campaign_status(self):
        paths = self._route_paths()
        assert "/dashboard/outreach/campaigns/{campaign_id}/status" in paths

    def test_outreach_log_activity(self):
        paths = self._route_paths()
        assert "/dashboard/outreach/campaigns/{campaign_id}/activity" in paths

    # -- NIF-204: Sales Queue --

    def test_queue_get(self):
        paths = self._route_paths()
        assert "/dashboard/queue" in paths

    def test_queue_claim(self):
        paths = self._route_paths()
        assert "/dashboard/queue/items/{item_id}/claim" in paths

    def test_queue_complete(self):
        paths = self._route_paths()
        assert "/dashboard/queue/items/{item_id}/complete" in paths

    def test_queue_skip(self):
        paths = self._route_paths()
        assert "/dashboard/queue/items/{item_id}/skip" in paths

    def test_queue_populate(self):
        paths = self._route_paths()
        assert "/dashboard/queue/populate" in paths

    # -- NIF-205: Qualification --

    def test_qualification_list(self):
        paths = self._route_paths()
        assert "/dashboard/qualification" in paths

    def test_qualification_evaluate(self):
        paths = self._route_paths()
        assert "/dashboard/qualification/evaluate/{restaurant_id}" in paths

    def test_qualification_review(self):
        paths = self._route_paths()
        assert "/dashboard/qualification/{result_id}/review" in paths

    def test_qualification_batch(self):
        paths = self._route_paths()
        assert "/dashboard/qualification/batch" in paths

    # -- NIF-206: Clusters --

    def test_clusters_list(self):
        paths = self._route_paths()
        assert "/dashboard/clusters" in paths

    def test_clusters_detect(self):
        paths = self._route_paths()
        assert "/dashboard/clusters/detect" in paths

    def test_clusters_detail(self):
        paths = self._route_paths()
        assert "/dashboard/clusters/{cluster_id}" in paths

    def test_clusters_expansion(self):
        paths = self._route_paths()
        assert "/dashboard/clusters/{cluster_id}/expansion-plan" in paths

    def test_clusters_launch(self):
        paths = self._route_paths()
        assert "/dashboard/clusters/{cluster_id}/launch-campaign" in paths

    def test_clusters_recalculate(self):
        paths = self._route_paths()
        assert "/dashboard/clusters/{cluster_id}/recalculate" in paths

    # -- NIF-207: Lead tasks --

    def test_lead_create_task(self):
        paths = self._route_paths()
        assert "/dashboard/leads/{lead_id}/tasks" in paths

    def test_lead_complete_task(self):
        paths = self._route_paths()
        assert "/dashboard/leads/tasks/{task_id}/complete" in paths

    def test_lead_merge(self):
        paths = self._route_paths()
        assert "/dashboard/leads/{lead_id}/merge" in paths

    def test_total_route_count(self):
        """We should have at least 44 routes (18 original + 26 new)."""
        assert len(router.routes) >= 44


class TestTemplateExistence:
    """Verify all templates exist and are loadable."""

    TEMPLATE_DIR = os.path.join("src", "dashboard", "templates")

    def test_base_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "base.html"))

    # Existing
    def test_leads_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "leads.html"))

    def test_lead_detail_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "lead_detail.html"))

    def test_jobs_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "jobs.html"))

    def test_restaurants_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "restaurants.html"))

    def test_prospects_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "prospects.html"))

    def test_analytics_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "analytics.html"))

    # New (NIF-202 through NIF-206)
    def test_neighborhoods_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "neighborhoods.html"))

    def test_outreach_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "outreach.html"))

    def test_queue_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "queue.html"))

    def test_qualification_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "qualification.html"))

    def test_clusters_template(self):
        assert os.path.exists(os.path.join(self.TEMPLATE_DIR, "clusters.html"))


class TestTemplateRendering:
    """Verify templates can be loaded by the Jinja2 engine."""

    def test_neighborhoods_loadable(self):
        t = templates.get_template("neighborhoods.html")
        assert t is not None

    def test_outreach_loadable(self):
        t = templates.get_template("outreach.html")
        assert t is not None

    def test_queue_loadable(self):
        t = templates.get_template("queue.html")
        assert t is not None

    def test_qualification_loadable(self):
        t = templates.get_template("qualification.html")
        assert t is not None

    def test_clusters_loadable(self):
        t = templates.get_template("clusters.html")
        assert t is not None

    def test_lead_detail_loadable(self):
        t = templates.get_template("lead_detail.html")
        assert t is not None

    def test_analytics_loadable(self):
        t = templates.get_template("analytics.html")
        assert t is not None


class TestBaseTemplateNavigation:
    """Verify base.html has all navigation tabs."""

    def _read_base(self):
        with open(os.path.join("src", "dashboard", "templates", "base.html")) as f:
            return f.read()

    def test_has_neighborhoods_tab(self):
        html = self._read_base()
        assert "/dashboard/neighborhoods" in html
        assert "Neighborhoods" in html

    def test_has_outreach_tab(self):
        html = self._read_base()
        assert "/dashboard/outreach" in html
        assert "Outreach" in html

    def test_has_queue_tab(self):
        html = self._read_base()
        assert "/dashboard/queue" in html
        assert "Queue" in html

    def test_has_qualification_tab(self):
        html = self._read_base()
        assert "/dashboard/qualification" in html
        assert "Qualification" in html

    def test_has_clusters_tab(self):
        html = self._read_base()
        assert "/dashboard/clusters" in html
        assert "Clusters" in html

    def test_existing_tabs_preserved(self):
        html = self._read_base()
        assert "/dashboard\"" in html or "/dashboard'" in html  # Leads
        assert "/dashboard/jobs" in html
        assert "/dashboard/restaurants" in html
        assert "/dashboard/prospects" in html
        assert "/dashboard/analytics" in html


class TestLeadDetailEnhancements:
    """NIF-207: Verify lead detail template has new sections."""

    def _read_lead_detail(self):
        with open(os.path.join("src", "dashboard", "templates", "lead_detail.html")) as f:
            return f.read()

    def test_has_follow_up_tasks_section(self):
        html = self._read_lead_detail()
        assert "Follow-up Tasks" in html

    def test_has_task_form(self):
        html = self._read_lead_detail()
        assert "hx-post" in html
        assert "/tasks" in html

    def test_has_task_complete_button(self):
        html = self._read_lead_detail()
        assert "tasks/" in html
        assert "/complete" in html

    def test_has_lifecycle_history(self):
        html = self._read_lead_detail()
        assert "Lifecycle History" in html

    def test_has_stage_history(self):
        html = self._read_lead_detail()
        assert "stage_history" in html

    def test_has_assignment_history(self):
        html = self._read_lead_detail()
        assert "assignment_history" in html


class TestNeighborhoodsTemplate:
    """NIF-202: Verify neighborhoods template structure."""

    def _read(self):
        with open(os.path.join("src", "dashboard", "templates", "neighborhoods.html")) as f:
            return f.read()

    def test_has_stats_cards(self):
        html = self._read()
        assert "opportunity" in html.lower()

    def test_has_filter_bar(self):
        html = self._read()
        assert "state" in html.lower()

    def test_has_refresh_button(self):
        html = self._read()
        assert "refresh" in html.lower()

    def test_has_comparison_section(self):
        html = self._read()
        assert "compare" in html.lower() or "comparison" in html.lower()


class TestOutreachTemplate:
    """NIF-203: Verify outreach template structure."""

    def _read(self):
        with open(os.path.join("src", "dashboard", "templates", "outreach.html")) as f:
            return f.read()

    def test_has_campaign_form(self):
        html = self._read()
        assert "campaign" in html.lower()

    def test_has_type_options(self):
        html = self._read()
        assert "email" in html.lower()

    def test_has_status_badges(self):
        html = self._read()
        assert "draft" in html.lower() or "active" in html.lower()


class TestQueueTemplate:
    """NIF-204: Verify queue template structure."""

    def _read(self):
        with open(os.path.join("src", "dashboard", "templates", "queue.html")) as f:
            return f.read()

    def test_has_rep_selector(self):
        html = self._read()
        assert "rep" in html.lower()

    def test_has_claim_action(self):
        html = self._read()
        assert "claim" in html.lower()

    def test_has_complete_action(self):
        html = self._read()
        assert "complete" in html.lower()

    def test_has_skip_action(self):
        html = self._read()
        assert "skip" in html.lower()


class TestQualificationTemplate:
    """NIF-205: Verify qualification template structure."""

    def _read(self):
        with open(os.path.join("src", "dashboard", "templates", "qualification.html")) as f:
            return f.read()

    def test_has_approve_button(self):
        html = self._read()
        assert "approve" in html.lower()

    def test_has_reject_button(self):
        html = self._read()
        assert "reject" in html.lower()

    def test_has_confidence_display(self):
        html = self._read()
        assert "confidence" in html.lower()

    def test_has_batch_evaluate(self):
        html = self._read()
        assert "batch" in html.lower()


class TestClustersTemplate:
    """NIF-206: Verify clusters template structure."""

    def _read(self):
        with open(os.path.join("src", "dashboard", "templates", "clusters.html")) as f:
            return f.read()

    def test_has_detect_form(self):
        html = self._read()
        assert "detect" in html.lower()

    def test_has_flywheel_display(self):
        html = self._read()
        assert "flywheel" in html.lower()

    def test_has_expansion_plan(self):
        html = self._read()
        assert "expansion" in html.lower()

    def test_has_launch_campaign(self):
        html = self._read()
        assert "launch" in html.lower() or "campaign" in html.lower()


class TestModelImports:
    """Verify all required models are imported in routes.py."""

    def _read_routes(self):
        with open(os.path.join("src", "dashboard", "routes.py")) as f:
            return f.read()

    def test_neighborhood_import(self):
        assert "Neighborhood" in self._read_routes()

    def test_outreach_imports(self):
        content = self._read_routes()
        assert "OutreachCampaign" in content
        assert "OutreachTarget" in content

    def test_queue_imports(self):
        content = self._read_routes()
        assert "RepQueueItem" in content

    def test_qualification_imports(self):
        content = self._read_routes()
        assert "QualificationResult" in content

    def test_cluster_imports(self):
        content = self._read_routes()
        assert "MerchantCluster" in content

    def test_task_import(self):
        content = self._read_routes()
        assert "FollowUpTask" in content

    def test_history_imports(self):
        content = self._read_routes()
        assert "LeadStageHistory" in content
        assert "LeadAssignmentHistory" in content

    def test_conversion_imports(self):
        content = self._read_routes()
        assert "ConversionFunnel" in content or "ConversionEvent" in content


class TestHTMXPatterns:
    """Verify HTMX attributes are properly used in templates."""

    def _read_all_templates(self):
        tpl_dir = os.path.join("src", "dashboard", "templates")
        content = ""
        for f in os.listdir(tpl_dir):
            if f.endswith(".html"):
                with open(os.path.join(tpl_dir, f)) as fh:
                    content += fh.read()
        return content

    def test_htmx_loaded(self):
        with open(os.path.join("src", "dashboard", "templates", "base.html")) as f:
            assert "htmx.org" in f.read()

    def test_hx_post_used(self):
        assert "hx-post" in self._read_all_templates()

    def test_hx_patch_used(self):
        assert "hx-patch" in self._read_all_templates()

    def test_hx_get_used(self):
        assert "hx-get" in self._read_all_templates()

    def test_hx_swap_used(self):
        assert "hx-swap" in self._read_all_templates()


class TestAppRegistration:
    """Verify dashboard router is registered in the app."""

    def test_router_in_app(self):
        from src.main import app
        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert any("/dashboard" in p for p in route_paths)

    def test_router_prefix(self):
        assert router.prefix == "/dashboard"

    def test_router_tags(self):
        assert "dashboard" in router.tags
