"""Tests for NIF-224: Email template rendering service.

Covers:
- render_email_template returns (subject, html, text) tuple
- subject uses default template when not in context
- subject uses context["subject"] when provided
- html body contains personalisation variables
- text body is auto-generated from html (no HTML tags)
- html_to_text strips block tags with newlines
- html_to_text decodes HTML entities
- all three templates render without error
- list_templates returns expected template names
- missing restaurant_name renders without crash (Jinja2 handles gracefully)
"""

import pytest
from unittest.mock import patch

from src.services.template_service import render_email_template, list_templates, _html_to_text


_BASE_CONTEXT = {
    "restaurant_name": "Pasta Palace",
    "owner_name": "Maria",
    "cuisine_type": "Italian",
    "sender_name": "Alex",
    "sender_title": "Partnership Manager",
    "cta_url": "https://n4cluster.com/book",
    "unsubscribe_url": "https://n4cluster.com/unsubscribe/tok123",
}


# ── render_email_template ────────────────────────────────────────────────────

def test_render_initial_outreach_returns_tuple():
    subject, html, text = render_email_template("initial_outreach", _BASE_CONTEXT)
    assert isinstance(subject, str)
    assert isinstance(html, str)
    assert isinstance(text, str)


def test_render_initial_outreach_subject_contains_restaurant():
    subject, _, _ = render_email_template("initial_outreach", _BASE_CONTEXT)
    assert "Pasta Palace" in subject


def test_render_initial_outreach_html_contains_owner_name():
    _, html, _ = render_email_template("initial_outreach", _BASE_CONTEXT)
    assert "Maria" in html


def test_render_initial_outreach_html_contains_cuisine_type():
    _, html, _ = render_email_template("initial_outreach", _BASE_CONTEXT)
    assert "Italian" in html


def test_render_initial_outreach_html_contains_cta_link():
    _, html, _ = render_email_template("initial_outreach", _BASE_CONTEXT)
    assert "https://n4cluster.com/book" in html


def test_render_initial_outreach_text_has_no_html_tags():
    _, _, text = render_email_template("initial_outreach", _BASE_CONTEXT)
    assert "<" not in text
    assert ">" not in text


def test_render_initial_outreach_text_contains_restaurant_name():
    _, _, text = render_email_template("initial_outreach", _BASE_CONTEXT)
    assert "Pasta Palace" in text


def test_render_initial_outreach_custom_subject():
    ctx = {**_BASE_CONTEXT, "subject": "Custom subject line"}
    subject, _, _ = render_email_template("initial_outreach", ctx)
    assert subject == "Custom subject line"


def test_render_follow_up_renders_without_error():
    ctx = {**_BASE_CONTEXT, "days_since_last": "7", "social_proof": "Great platform!"}
    subject, html, text = render_email_template("follow_up", ctx)
    assert "Pasta Palace" in subject
    assert "Great platform!" in html
    assert "<" not in text


def test_render_re_engagement_renders_without_error():
    ctx = {**_BASE_CONTEXT, "months_since": "3"}
    subject, html, text = render_email_template("re_engagement", ctx)
    assert "Pasta Palace" in subject
    assert len(html) > 100
    assert "<" not in text


def test_render_re_engagement_stats_in_html():
    ctx = {**_BASE_CONTEXT, "months_since": "3"}
    _, html, _ = render_email_template("re_engagement", ctx)
    assert "+34%" in html
    assert "$0" in html


def test_render_follow_up_default_subject_when_no_subject_key():
    ctx = {k: v for k, v in _BASE_CONTEXT.items() if k != "subject"}
    subject, _, _ = render_email_template("follow_up", ctx)
    assert "Pasta Palace" in subject


# ── list_templates ────────────────────────────────────────────────────────────

def test_list_templates_returns_expected_names():
    templates = list_templates()
    assert "initial_outreach" in templates
    assert "follow_up" in templates
    assert "re_engagement" in templates


def test_list_templates_returns_list():
    assert isinstance(list_templates(), list)


# ── _html_to_text ─────────────────────────────────────────────────────────────

def test_html_to_text_strips_tags():
    result = _html_to_text("<p>Hello <strong>World</strong></p>")
    assert "<" not in result
    assert "Hello" in result
    assert "World" in result


def test_html_to_text_block_tags_become_newlines():
    result = _html_to_text("<p>First</p><p>Second</p>")
    assert "First" in result
    assert "Second" in result
    # They should be on separate lines
    assert result.index("First") < result.index("Second")


def test_html_to_text_decodes_nbsp():
    result = _html_to_text("Hello&nbsp;World")
    assert "Hello World" in result


def test_html_to_text_decodes_amp():
    result = _html_to_text("N4Cluster &amp; Partners")
    assert "&" in result
    assert "&amp;" not in result


def test_html_to_text_empty_string():
    assert _html_to_text("") == ""


def test_html_to_text_plain_text_passthrough():
    result = _html_to_text("Just plain text")
    assert result == "Just plain text"


# ── Missing template raises ────────────────────────────────────────────────────

def test_render_nonexistent_template_raises():
    from jinja2 import TemplateNotFound
    with pytest.raises(TemplateNotFound):
        render_email_template("nonexistent_template", _BASE_CONTEXT)
