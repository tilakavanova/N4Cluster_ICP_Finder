"""Tests for SMS URL shortener service (NIF-233).

Covers:
- shorten_url generates a short tracking URL
- shorten_url stores token in Redis via tracking_tokens
- replace_urls_in_message replaces all URLs in message body
- replace_urls_in_message leaves non-URL text intact
- replace_urls_in_message handles message with no URLs
"""

from unittest.mock import MagicMock, patch

from src.services.url_shortener import replace_urls_in_message, shorten_url


class TestShortenUrl:
    def test_returns_short_url(self):
        with patch("src.services.url_shortener.generate_tracking_token", return_value="abc12345"), \
             patch("src.services.url_shortener.store_tracking_token") as mock_store:
            result = shorten_url(
                "https://example.com/page",
                lead_id="lead-1",
                campaign_id="camp-1",
                target_id="target-1",
                base_url="https://n4cluster.com",
            )

        assert result == "https://n4cluster.com/t/s/abc12345"
        mock_store.assert_called_once()
        stored_data = mock_store.call_args[0][1]
        assert stored_data["url"] == "https://example.com/page"
        assert stored_data["channel"] == "sms"
        assert stored_data["lead_id"] == "lead-1"

    def test_uses_default_base_url(self):
        with patch("src.services.url_shortener.generate_tracking_token", return_value="xyz"), \
             patch("src.services.url_shortener.store_tracking_token"), \
             patch("src.services.url_shortener.settings") as mock_settings:
            mock_settings.tracking_base_url = "https://default.example.com"
            result = shorten_url("https://example.com", lead_id="l", campaign_id="c")

        assert result.startswith("https://default.example.com/t/s/")


class TestReplaceUrlsInMessage:
    def test_replaces_single_url(self):
        counter = {"n": 0}

        def mock_token():
            counter["n"] += 1
            return f"tok{counter['n']}"

        with patch("src.services.url_shortener.generate_tracking_token", side_effect=mock_token), \
             patch("src.services.url_shortener.store_tracking_token"):
            result = replace_urls_in_message(
                "Visit https://example.com/deals for offers!",
                lead_id="l1",
                campaign_id="c1",
                base_url="https://t.co",
            )

        assert "https://example.com/deals" not in result
        assert "https://t.co/t/s/tok1" in result
        assert "for offers!" in result

    def test_replaces_multiple_urls(self):
        counter = {"n": 0}

        def mock_token():
            counter["n"] += 1
            return f"tok{counter['n']}"

        with patch("src.services.url_shortener.generate_tracking_token", side_effect=mock_token), \
             patch("src.services.url_shortener.store_tracking_token"):
            result = replace_urls_in_message(
                "Check https://a.com and http://b.com now",
                lead_id="l1",
                campaign_id="c1",
                base_url="https://t.co",
            )

        assert "tok1" in result
        assert "tok2" in result
        assert "https://a.com" not in result
        assert "http://b.com" not in result

    def test_no_urls_unchanged(self):
        with patch("src.services.url_shortener.generate_tracking_token"), \
             patch("src.services.url_shortener.store_tracking_token"):
            result = replace_urls_in_message(
                "Hello! No URLs here.",
                lead_id="l1",
                campaign_id="c1",
            )

        assert result == "Hello! No URLs here."

    def test_preserves_surrounding_text(self):
        with patch("src.services.url_shortener.generate_tracking_token", return_value="short"), \
             patch("src.services.url_shortener.store_tracking_token"):
            result = replace_urls_in_message(
                "Hi Joe, visit https://example.com to learn more. Thanks!",
                lead_id="l1",
                campaign_id="c1",
                base_url="https://t.co",
            )

        assert result.startswith("Hi Joe, visit ")
        assert result.endswith(" to learn more. Thanks!")
