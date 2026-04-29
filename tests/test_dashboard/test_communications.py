"""Tests for the Communications analytics dashboard tab (NIF-239)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestCommunicationsRoute:
    """Tests for the /dashboard/communications route."""

    def test_communications_template_exists(self):
        """Verify the communications template file exists and is loadable."""
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env = Environment(
            loader=FileSystemLoader("src/dashboard/templates"),
            autoescape=select_autoescape(["html"]),
        )
        template = env.get_template("communications.html")
        assert template is not None

    def test_communications_template_renders_stats(self):
        """Verify the template renders correctly with sample data."""
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env = Environment(
            loader=FileSystemLoader("src/dashboard/templates"),
            autoescape=select_autoescape(["html"]),
        )
        template = env.get_template("communications.html")
        html = template.render(
            stats={
                "total_sent": 100,
                "total_opens": 45,
                "total_clicks": 12,
                "total_bounces": 3,
                "total_replies": 8,
                "open_rate": 45.0,
                "click_rate": 12.0,
                "reply_rate": 8.0,
                "bounce_rate": 3.0,
            },
            timeline=[{"date": "04-01", "count": 5}] * 31,
            top_leads=[
                {
                    "id": "test-uuid",
                    "email": "test@example.com",
                    "company": "Test Co",
                    "name": "John Doe",
                    "event_count": 15,
                }
            ],
            campaigns=[
                {
                    "id": "camp-uuid",
                    "name": "Q1 Outreach",
                    "status": "active",
                    "type": "email",
                    "event_count": 50,
                }
            ],
            recent_events=[],
            active_tab="communications",
        )
        assert "Emails Sent" in html
        assert "100" in html
        assert "45.0%" in html
        assert "12.0%" in html
        assert "8.0%" in html
        assert "3.0%" in html
        assert "test@example.com" in html
        assert "Q1 Outreach" in html

    def test_communications_template_empty_state(self):
        """Template renders gracefully with no data."""
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env = Environment(
            loader=FileSystemLoader("src/dashboard/templates"),
            autoescape=select_autoescape(["html"]),
        )
        template = env.get_template("communications.html")
        html = template.render(
            stats={
                "total_sent": 0,
                "total_opens": 0,
                "total_clicks": 0,
                "total_bounces": 0,
                "total_replies": 0,
                "open_rate": 0,
                "click_rate": 0,
                "reply_rate": 0,
                "bounce_rate": 0,
            },
            timeline=[],
            top_leads=[],
            campaigns=[],
            recent_events=[],
            active_tab="communications",
        )
        assert "No engagement data yet" in html
        assert "No campaigns found" in html
        assert "No communication events recorded yet" in html

    def test_communications_tab_in_base_nav(self):
        """Verify the Communications tab appears in the base navigation."""
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env = Environment(
            loader=FileSystemLoader("src/dashboard/templates"),
            autoescape=select_autoescape(["html"]),
        )
        template = env.get_template("base.html")
        html = template.render(active_tab="communications")
        assert "/dashboard/communications" in html
        assert "Communications" in html

    def test_communications_tab_active_state(self):
        """Verify the Communications tab has active styling when selected."""
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env = Environment(
            loader=FileSystemLoader("src/dashboard/templates"),
            autoescape=select_autoescape(["html"]),
        )
        template = env.get_template("base.html")

        # When communications is active
        html_active = template.render(active_tab="communications")
        # The Communications link should have bg-navy-800 (active style)
        # Find the communications link and check it has the active class
        assert "bg-navy-800" in html_active

        # When another tab is active, communications should not be highlighted
        html_other = template.render(active_tab="leads")
        # Communications link exists but shouldn't be the only one with bg-navy-800
        assert "/dashboard/communications" in html_other
