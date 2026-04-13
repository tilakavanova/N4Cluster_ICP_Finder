"""Regression tests for dashboard defects NIF-209 through NIF-214."""

import os
import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from unittest.mock import MagicMock


@pytest.fixture(scope="module")
def jinja_env():
    return Environment(
        loader=FileSystemLoader("src/dashboard/templates"),
        autoescape=select_autoescape(["html"]),
    )


class TestNIF209_JobsAutoRefresh:
    """NIF-209: Crawl jobs table should auto-refresh frequently to show running status."""

    def test_jobs_table_polls_every_5s(self):
        with open("src/dashboard/templates/jobs.html") as f:
            html = f.read()
        assert 'hx-trigger="every 5s"' in html

    def test_jobs_table_has_htmx_get(self):
        with open("src/dashboard/templates/jobs.html") as f:
            html = f.read()
        assert 'hx-get="/dashboard/jobs/list"' in html

    def test_create_job_uses_background_task(self):
        with open("src/dashboard/routes.py") as f:
            content = f.read()
        assert "asyncio.create_task" in content
        assert "_run_crawl_background" in content

    def test_create_job_sets_running_status_before_redirect(self):
        with open("src/dashboard/routes.py") as f:
            content = f.read()
        # The job should be created with status="running" before redirect
        idx_running = content.index('status="running"')
        idx_redirect = content.index("Crawl started:")
        assert idx_running < idx_redirect


class TestNIF210_RestaurantStatsFilter:
    """NIF-210: Restaurant metrics bar should refresh based on applied filters."""

    def test_stats_use_filter_function(self):
        with open("src/dashboard/routes.py") as f:
            content = f.read()
        assert "_apply_restaurant_filters" in content

    def test_filtered_stats_include_city(self):
        with open("src/dashboard/routes.py") as f:
            content = f.read()
        # The filter helper should check city
        idx = content.index("_apply_restaurant_filters")
        section = content[idx:idx+500]
        assert "city" in section

    def test_filtered_stats_include_state(self):
        with open("src/dashboard/routes.py") as f:
            content = f.read()
        idx = content.index("_apply_restaurant_filters")
        section = content[idx:idx+500]
        assert "state" in section


class TestNIF211_RestaurantColumns:
    """NIF-211: Fit, Delivery, POS columns should be populated in restaurant table."""

    def test_no_colspan_on_icp_score_cell(self):
        with open("src/dashboard/templates/restaurants.html") as f:
            html = f.read()
        # The ICP score cell should NOT have colspan="2" — that was merging Fit column
        assert 'colspan="2"' not in html

    def test_fit_label_column_exists(self):
        with open("src/dashboard/templates/restaurants.html") as f:
            html = f.read()
        assert "r.icp_score.fit_label" in html

    def test_fit_label_has_color_badges(self):
        with open("src/dashboard/templates/restaurants.html") as f:
            html = f.read()
        assert "excellent" in html
        assert "good" in html
        assert "moderate" in html

    def test_delivery_column_checks_icp_score(self):
        with open("src/dashboard/templates/restaurants.html") as f:
            html = f.read()
        assert "r.icp_score.has_delivery" in html

    def test_pos_column_checks_icp_score(self):
        with open("src/dashboard/templates/restaurants.html") as f:
            html = f.read()
        assert "r.icp_score.pos_provider" in html or "r.icp_score.has_pos" in html


class TestNIF212_NeighborhoodDelivery:
    """NIF-212: Delivery coverage should not show '&mdash;%' when value is 0."""

    def test_delivery_uses_is_not_none_check(self):
        with open("src/dashboard/templates/neighborhoods.html") as f:
            html = f.read()
        assert "delivery_coverage is not none" in html

    def test_independent_ratio_uses_is_not_none_check(self):
        with open("src/dashboard/templates/neighborhoods.html") as f:
            html = f.read()
        assert "independent_ratio is not none" in html

    def test_zero_delivery_renders_zero_percent(self, jinja_env):
        """When delivery_coverage is 0.0, should show '0%' not '&mdash;%'."""
        t = jinja_env.get_template("neighborhoods.html")
        html = t.render(
            neighborhoods=[{
                "rank": 1, "zip_code": "10001", "name": "Test",
                "city": "Test", "state": "NY",
                "restaurant_count": 5, "avg_icp_score": 50.0,
                "opportunity_score": 40.0,
                "independent_ratio": 0.0,
                "delivery_coverage": 0.0,
                "top_cuisines": [],
            }],
            filters={"state": "", "city": "", "min_restaurants": 3},
            page=1, total_pages=1, message="", active_tab="neighborhoods",
        )
        # Should contain "0%" for both columns, not "&mdash;%"
        assert "&amp;mdash;%" not in html
        assert "0%" in html


class TestNIF213_ComparisonDelivery:
    """NIF-213: Comparison grid delivery should not show '&mdash;%' when value is 0."""

    def test_comparison_uses_is_not_none_check(self):
        with open("src/dashboard/templates/neighborhoods_compare.html") as f:
            html = f.read()
        assert "delivery_coverage is not none" in html
        assert "independent_ratio is not none" in html

    def test_zero_values_render_zero_percent(self, jinja_env):
        t = jinja_env.get_template("neighborhoods_compare.html")
        html = t.render(comparison={
            "neighborhoods": [{
                "zip_code": "10001", "name": "Test",
                "restaurant_count": 5, "avg_icp_score": 50.0,
                "opportunity_score": 40.0,
                "independent_ratio": 0.0,
                "delivery_coverage": 0.0,
            }],
            "winners": {},
        })
        assert "&amp;mdash;%" not in html
        assert "0%" in html


class TestNIF214_QualificationTab:
    """NIF-214: Qualification tab should work — restaurant relationship must be eagerly loaded."""

    def test_list_pending_review_loads_restaurant(self):
        with open("src/services/qualification.py") as f:
            content = f.read()
        # Must eagerly load restaurant relationship
        assert "selectinload(QualificationResult.restaurant)" in content

    def test_qualification_template_renders_empty(self, jinja_env):
        t = jinja_env.get_template("qualification.html")
        html = t.render(
            pending=[],
            stats={"qualified": 0, "not_qualified": 0, "needs_review": 0},
            message="",
            active_tab="qualification",
        )
        assert "Qualification" in html or "qualification" in html

    def test_qualification_template_renders_with_mock_data(self, jinja_env):
        """Simulate a pending review item with restaurant relationship."""
        result = MagicMock()
        result.id = "test-id"
        result.restaurant_id = "rest-id"
        result.confidence_score = 0.55
        result.qualification_status = "needs_review"
        result.signals_summary = ["ICP: 65", "Independent: Yes"]
        restaurant = MagicMock()
        restaurant.name = "Test Restaurant"
        restaurant.city = "Boston"
        restaurant.state = "MA"
        result.restaurant = restaurant

        t = jinja_env.get_template("qualification.html")
        html = t.render(
            pending=[result],
            stats={"qualified": 5, "not_qualified": 2, "needs_review": 1},
            message="",
            active_tab="qualification",
        )
        assert "Test Restaurant" in html
        assert "Boston" in html
        assert "Approve" in html
        assert "Reject" in html
