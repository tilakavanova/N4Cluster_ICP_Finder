"""Tests for NIF-219: SendGrid email delivery client.

Covers:
- SendGridClient initialisation with config values
- send_email builds correct Mail object with tracking settings enabled
- send_email_with_template uses dynamic templates correctly
- Tracking pixel is injected before </body> tag
- All links in HTML body are wrapped with tracking URLs
- List-Unsubscribe header is set correctly
- send_email returns (True, message_id, None) on success
- send_email returns (False, None, error_message) on API failure
- Categories are passed through to SendGrid API
"""

from unittest.mock import MagicMock, patch, call

import pytest

from src.services.sendgrid_client import SendGridClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TRACKING = {
    "lead_id": "lead-001",
    "campaign_id": "camp-001",
    "target_id": "tgt-001",
}

SIMPLE_HTML = "<html><body><p>Hello</p></body></html>"
HTML_WITH_LINK = '<html><body><a href="https://example.com">Click</a></body></html>'
HTML_NO_BODY_TAG = "<p>No body tag here</p>"


def _make_client(**kwargs) -> SendGridClient:
    """Build a SendGridClient with a mocked underlying SDK client."""
    defaults = dict(
        api_key="SG.test_key",
        from_email="outreach@n4cluster.com",
        from_name="N4Cluster",
        tracking_base_url="https://track.n4cluster.com",
        categories=["outreach"],
    )
    defaults.update(kwargs)
    return SendGridClient(**defaults)


def _mock_success_response(message_id: str = "msg-abc123"):
    resp = MagicMock()
    resp.status_code = 202
    resp.headers = {"X-Message-Id": message_id}
    return resp


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestSendGridClientInit:
    def test_stores_from_email(self):
        client = _make_client()
        assert client.from_email == "outreach@n4cluster.com"

    def test_stores_from_name(self):
        client = _make_client()
        assert client.from_name == "N4Cluster"

    def test_stores_tracking_base_url(self):
        client = _make_client(tracking_base_url="https://custom.example.com")
        assert client.tracking_base_url == "https://custom.example.com"

    def test_default_categories(self):
        client = _make_client(categories=None)
        assert client.categories == ["outreach"]

    def test_custom_categories(self):
        client = _make_client(categories=["outreach", "follow-up"])
        assert client.categories == ["outreach", "follow-up"]

    def test_creates_sendgrid_sdk_client(self):
        with patch("src.services.sendgrid_client.SendGridAPIClient") as mock_sg:
            _make_client(api_key="SG.mykey")
            mock_sg.assert_called_once_with(api_key="SG.mykey")


# ---------------------------------------------------------------------------
# send_email — success / failure contract
# ---------------------------------------------------------------------------

class TestSendEmailContract:
    def _client_with_mock(self, response):
        client = _make_client()
        client._client = MagicMock()
        client._client.send.return_value = response
        return client

    def test_returns_true_message_id_none_on_success(self):
        client = self._client_with_mock(_mock_success_response("msg-xyz"))
        success, message_id, error = client.send_email(
            "user@example.com", "Subject", SIMPLE_HTML
        )
        assert success is True
        assert message_id == "msg-xyz"
        assert error is None

    def test_returns_false_none_error_on_api_failure(self):
        client = _make_client()
        client._client = MagicMock()
        client._client.send.side_effect = Exception("401 Unauthorized")
        success, message_id, error = client.send_email(
            "user@example.com", "Subject", SIMPLE_HTML
        )
        assert success is False
        assert message_id is None
        assert "401 Unauthorized" in error

    def test_message_id_none_when_header_missing(self):
        resp = MagicMock()
        resp.headers = {}
        client = self._client_with_mock(resp)
        success, message_id, error = client.send_email(
            "user@example.com", "Subject", SIMPLE_HTML
        )
        assert success is True
        assert message_id is None


# ---------------------------------------------------------------------------
# send_email — Mail object construction
# ---------------------------------------------------------------------------

class TestSendEmailMailObject:
    def _capture_sent_message(self, html=SIMPLE_HTML, tracking_data=None):
        """Returns the Mail object passed to sendgrid client.send()."""
        client = _make_client()
        client._client = MagicMock()
        client._client.send.return_value = _mock_success_response()
        # Disable pixel/URL wrapping so we test structure in isolation
        with patch.object(client, "_process_html", side_effect=lambda h, t: h):
            client.send_email(
                "user@example.com", "Test Subject", html,
                tracking_data=tracking_data or {}
            )
        return client._client.send.call_args[0][0]

    def test_to_address_set(self):
        msg = self._capture_sent_message()
        # The Mail object stores personalizations; extract the 'to' list
        to_emails = [p.tos[0]["email"] for p in msg.personalizations]
        assert "user@example.com" in to_emails

    def test_subject_set(self):
        msg = self._capture_sent_message()
        assert msg.subject.get() == "Test Subject"

    def test_from_email_and_name_set(self):
        msg = self._capture_sent_message()
        assert msg.from_email.email == "outreach@n4cluster.com"
        assert msg.from_email.name == "N4Cluster"

    def test_click_tracking_enabled(self):
        msg = self._capture_sent_message()
        ct = msg.tracking_settings.click_tracking
        assert ct.enable is True

    def test_open_tracking_enabled(self):
        msg = self._capture_sent_message()
        ot = msg.tracking_settings.open_tracking
        assert ot.enable is True

    def test_list_unsubscribe_header_present(self):
        msg = self._capture_sent_message()
        # msg.headers is a list of Header objects with .key / .value
        header_keys = [h.key for h in (msg.headers or [])]
        assert "List-Unsubscribe" in header_keys

    def test_categories_attached(self):
        client = _make_client(categories=["outreach", "follow-up"])
        client._client = MagicMock()
        client._client.send.return_value = _mock_success_response()
        with patch.object(client, "_process_html", side_effect=lambda h, t: h):
            client.send_email("u@example.com", "S", SIMPLE_HTML)
        msg = client._client.send.call_args[0][0]
        cat_names = [c.name for c in (msg.categories or [])]
        assert "outreach" in cat_names
        assert "follow-up" in cat_names

    def test_plain_text_content_attached_when_provided(self):
        client = _make_client()
        client._client = MagicMock()
        client._client.send.return_value = _mock_success_response()
        with patch.object(client, "_process_html", side_effect=lambda h, t: h):
            client.send_email("u@example.com", "S", SIMPLE_HTML, text_content="Hello plain")
        msg = client._client.send.call_args[0][0]
        # plain_text_content is stored on the Mail object
        assert msg.plain_text_content is not None


# ---------------------------------------------------------------------------
# send_email_with_template
# ---------------------------------------------------------------------------

class TestSendEmailWithTemplate:
    def test_template_id_set(self):
        client = _make_client()
        client._client = MagicMock()
        client._client.send.return_value = _mock_success_response()
        client.send_email_with_template(
            "user@example.com", "d-abc123", {"name": "Alice"}
        )
        msg = client._client.send.call_args[0][0]
        assert msg.template_id.get() == "d-abc123"

    def test_dynamic_template_data_set(self):
        client = _make_client()
        client._client = MagicMock()
        client._client.send.return_value = _mock_success_response()
        client.send_email_with_template(
            "user@example.com", "d-abc123", {"name": "Alice", "score": 85}
        )
        msg = client._client.send.call_args[0][0]
        dtd = msg.personalizations[0].dynamic_template_data
        assert dtd == {"name": "Alice", "score": 85}

    def test_from_address_set(self):
        client = _make_client()
        client._client = MagicMock()
        client._client.send.return_value = _mock_success_response()
        client.send_email_with_template("u@example.com", "d-x", {})
        msg = client._client.send.call_args[0][0]
        assert msg.from_email.email == "outreach@n4cluster.com"

    def test_returns_success_tuple(self):
        client = _make_client()
        client._client = MagicMock()
        client._client.send.return_value = _mock_success_response("msg-tmpl")
        success, message_id, error = client.send_email_with_template(
            "user@example.com", "d-abc123", {}
        )
        assert success is True
        assert message_id == "msg-tmpl"
        assert error is None

    def test_returns_failure_tuple_on_exception(self):
        client = _make_client()
        client._client = MagicMock()
        client._client.send.side_effect = Exception("template not found")
        success, message_id, error = client.send_email_with_template(
            "u@example.com", "d-bad", {}
        )
        assert success is False
        assert message_id is None
        assert "template not found" in error

    def test_tracking_settings_applied(self):
        client = _make_client()
        client._client = MagicMock()
        client._client.send.return_value = _mock_success_response()
        client.send_email_with_template("u@example.com", "d-x", {})
        msg = client._client.send.call_args[0][0]
        assert msg.tracking_settings.click_tracking.enable is True
        assert msg.tracking_settings.open_tracking.enable is True

    def test_categories_applied_to_template_email(self):
        client = _make_client(categories=["outreach"])
        client._client = MagicMock()
        client._client.send.return_value = _mock_success_response()
        client.send_email_with_template("u@example.com", "d-x", {})
        msg = client._client.send.call_args[0][0]
        cat_names = [c.name for c in (msg.categories or [])]
        assert "outreach" in cat_names


# ---------------------------------------------------------------------------
# Pixel injection
# ---------------------------------------------------------------------------

class TestPixelInjection:
    def test_pixel_injected_before_body_close(self):
        client = _make_client(tracking_base_url="https://track.n4cluster.com")
        with patch("src.services.sendgrid_client.generate_pixel_url", return_value="https://track.n4cluster.com/px/tok123.gif"):
            html = client._inject_pixel(
                "<html><body><p>Hi</p></body></html>", TRACKING
            )
        assert "tok123.gif" in html
        assert html.index("tok123.gif") < html.index("</body>")

    def test_pixel_appended_when_no_body_tag(self):
        client = _make_client()
        with patch("src.services.sendgrid_client.generate_pixel_url", return_value="https://track.n4cluster.com/px/tok999.gif"):
            html = client._inject_pixel(HTML_NO_BODY_TAG, TRACKING)
        assert "tok999.gif" in html

    def test_pixel_not_injected_without_tracking_data(self):
        client = _make_client()
        with patch("src.services.sendgrid_client.generate_pixel_url") as mock_px:
            html = client._inject_pixel(SIMPLE_HTML, {})
        mock_px.assert_not_called()
        assert html == SIMPLE_HTML

    def test_pixel_uses_tracking_base_url(self):
        client = _make_client(tracking_base_url="https://custom.track.io")
        with patch("src.services.sendgrid_client.generate_pixel_url", return_value="https://custom.track.io/px/abc.gif") as mock_px:
            client._inject_pixel("<body></body>", TRACKING)
        mock_px.assert_called_once_with(
            lead_id="lead-001",
            campaign_id="camp-001",
            target_id="tgt-001",
            base_url="https://custom.track.io",
            redis_client=None,
        )


# ---------------------------------------------------------------------------
# URL wrapping
# ---------------------------------------------------------------------------

class TestURLWrapping:
    def test_links_are_wrapped(self):
        client = _make_client(tracking_base_url="https://track.n4cluster.com")
        with patch("src.services.sendgrid_client.wrap_url", return_value="https://track.n4cluster.com/t/tok456") as mock_wrap:
            html = client._wrap_links(HTML_WITH_LINK, TRACKING)
        mock_wrap.assert_called_once()
        assert "https://track.n4cluster.com/t/tok456" in html

    def test_mailto_links_not_wrapped(self):
        client = _make_client()
        html = '<a href="mailto:info@example.com">Email us</a>'
        with patch("src.services.sendgrid_client.wrap_url") as mock_wrap:
            result = client._wrap_links(html, TRACKING)
        mock_wrap.assert_not_called()
        assert "mailto:info@example.com" in result

    def test_anchor_links_not_wrapped(self):
        client = _make_client()
        html = '<a href="#section1">Jump</a>'
        with patch("src.services.sendgrid_client.wrap_url") as mock_wrap:
            result = client._wrap_links(html, TRACKING)
        mock_wrap.assert_not_called()
        assert "#section1" in result

    def test_no_wrapping_without_lead_and_campaign(self):
        client = _make_client()
        with patch("src.services.sendgrid_client.wrap_url") as mock_wrap:
            result = client._wrap_links(HTML_WITH_LINK, {})
        mock_wrap.assert_not_called()
        assert result == HTML_WITH_LINK

    def test_multiple_links_all_wrapped(self):
        client = _make_client()
        html = (
            '<a href="https://a.com">A</a>'
            '<a href="https://b.com">B</a>'
        )
        wrapped_urls = iter(["https://track/t/t1", "https://track/t/t2"])
        with patch("src.services.sendgrid_client.wrap_url", side_effect=lambda **kw: next(wrapped_urls)):
            result = client._wrap_links(html, TRACKING)
        assert "https://track/t/t1" in result
        assert "https://track/t/t2" in result

    def test_wrap_url_called_with_correct_args(self):
        client = _make_client(tracking_base_url="https://track.n4cluster.com")
        with patch("src.services.sendgrid_client.wrap_url", return_value="https://track.n4cluster.com/t/tok") as mock_wrap:
            client._wrap_links('<a href="https://example.com/page">X</a>', TRACKING)
        mock_wrap.assert_called_once_with(
            original_url="https://example.com/page",
            lead_id="lead-001",
            campaign_id="camp-001",
            target_id="tgt-001",
            channel="email",
            base_url="https://track.n4cluster.com",
            redis_client=None,
        )


# ---------------------------------------------------------------------------
# List-Unsubscribe header
# ---------------------------------------------------------------------------

class TestUnsubscribeHeader:
    def _header_dict(self, msg) -> dict:
        """Flatten Mail.headers (list of Header objects) into a plain dict."""
        return {h.key: h.value for h in (msg.headers or [])}

    def test_list_unsubscribe_header_contains_email(self):
        client = _make_client()
        from sendgrid.helpers.mail import Mail
        msg = Mail()
        client._apply_unsubscribe_header(msg, "user@example.com")
        headers = self._header_dict(msg)
        assert "List-Unsubscribe" in headers
        assert "user@example.com" in headers["List-Unsubscribe"]

    def test_list_unsubscribe_header_contains_unsubscribe_url(self):
        client = _make_client(tracking_base_url="https://track.n4cluster.com")
        from sendgrid.helpers.mail import Mail
        msg = Mail()
        client._apply_unsubscribe_header(msg, "user@example.com")
        headers = self._header_dict(msg)
        assert "List-Unsubscribe" in headers
        assert "unsubscribe" in headers["List-Unsubscribe"].lower()

    def test_list_unsubscribe_post_header_set(self):
        client = _make_client()
        from sendgrid.helpers.mail import Mail
        msg = Mail()
        client._apply_unsubscribe_header(msg, "u@example.com")
        headers = self._header_dict(msg)
        assert "List-Unsubscribe-Post" in headers
        assert "One-Click" in headers["List-Unsubscribe-Post"]
