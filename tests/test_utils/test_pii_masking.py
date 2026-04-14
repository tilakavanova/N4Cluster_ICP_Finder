"""Tests for NIF-252: PII masking utilities and structlog integration."""

import io
import json
import pytest
import structlog
from unittest.mock import patch

from src.utils.pii_masking import mask_email, mask_phone, mask_dict_pii


# ── mask_email ─────────────────────────────────────────────────────────────

class TestMaskEmail:
    def test_typical_address(self):
        assert mask_email("tilak@example.com") == "ti***@example.com"

    def test_short_local_part_one_char(self):
        assert mask_email("a@x.com") == "a***@x.com"

    def test_short_local_part_two_chars(self):
        assert mask_email("ab@x.com") == "ab***@x.com"

    def test_exactly_three_chars(self):
        assert mask_email("foo@bar.com") == "fo***@bar.com"

    def test_subdomain_preserved(self):
        result = mask_email("user@mail.company.io")
        assert result.endswith("@mail.company.io")
        assert result.startswith("us***")

    def test_non_string_passthrough(self):
        assert mask_email(None) is None  # type: ignore[arg-type]

    def test_string_without_at_passthrough(self):
        assert mask_email("notanemail") == "notanemail"

    def test_empty_string(self):
        assert mask_email("") == ""


# ── mask_phone ─────────────────────────────────────────────────────────────

class TestMaskPhone:
    def test_plain_digits(self):
        assert mask_phone("5551234567") == "555***67"

    def test_formatted_us(self):
        assert mask_phone("(555) 123-4567") == "555***67"

    def test_international(self):
        result = mask_phone("+1-800-555-0199")
        assert "***" in result
        assert result.endswith("99")

    def test_too_short_passthrough(self):
        assert mask_phone("123") == "123"

    def test_non_string_passthrough(self):
        assert mask_phone(None) is None  # type: ignore[arg-type]

    def test_exactly_five_digits(self):
        result = mask_phone("12345")
        assert "***" in result


# ── mask_dict_pii ──────────────────────────────────────────────────────────

class TestMaskDictPii:
    def test_email_key_masked(self):
        event = {"event": "signup", "email": "user@example.com"}
        result = mask_dict_pii(None, "info", event)
        assert "***" in result["email"]
        assert result["event"] == "signup"

    def test_phone_key_masked(self):
        event = {"event": "call", "phone": "5551234567"}
        result = mask_dict_pii(None, "info", event)
        assert "***" in result["phone"]

    def test_all_pii_keys_masked(self):
        event = {
            "to_email": "a@b.com",
            "from_email": "c@d.com",
            "recipient": "e@f.com",
            "sender": "g@h.com",
            "email_address": "i@j.com",
            "phone_number": "5559876543",
            "safe_field": "keep_me",
        }
        result = mask_dict_pii(None, "info", event)
        for key in ("to_email", "from_email", "recipient", "sender", "email_address"):
            assert "***" in result[key], f"{key} not masked"
        assert "***" in result["phone_number"]
        assert result["safe_field"] == "keep_me"

    def test_case_insensitive_key(self):
        event = {"EMAIL": "user@example.com", "Phone": "5551234567"}
        result = mask_dict_pii(None, "info", event)
        assert "***" in result["EMAIL"]
        assert "***" in result["Phone"]

    def test_nested_dict(self):
        event = {
            "event": "outreach",
            "contact": {"email": "nested@example.com", "name": "Alice"},
        }
        result = mask_dict_pii(None, "info", event)
        assert "***" in result["contact"]["email"]
        assert result["contact"]["name"] == "Alice"

    def test_nested_list_of_dicts(self):
        event = {
            "recipients": [
                {"email": "a@b.com", "name": "A"},
                {"email": "c@d.com", "name": "C"},
            ]
        }
        result = mask_dict_pii(None, "info", event)
        for r in result["recipients"]:
            assert "***" in r["email"]

    def test_non_pii_fields_untouched(self):
        event = {"restaurant": "Joe's Pizza", "score": 82.5, "city": "NYC"}
        result = mask_dict_pii(None, "info", event)
        assert result == event

    def test_none_value_handled(self):
        event = {"email": None}
        result = mask_dict_pii(None, "info", event)
        assert result["email"] is None


# ── Full-chain: structlog with PII masking in production mode ─────────────

class TestStructlogIntegration:
    def test_production_mode_masks_pii(self):
        """In production (debug=False), JSON output should contain masked email."""
        output = io.StringIO()

        with patch("src.utils.logging.settings") as mock_cfg:
            mock_cfg.debug = False
            mock_cfg.log_level = "INFO"

            from src.utils.logging import setup_logging
            setup_logging()

        structlog.configure(
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                mask_dict_pii,
                structlog.processors.JSONRenderer(),
            ],
            logger_factory=structlog.PrintLoggerFactory(file=output),
            cache_logger_on_first_use=False,
        )

        log = structlog.get_logger("test")
        log.info("user_signup", email="foo@bar.com")

        logged = output.getvalue()
        assert logged, "Nothing was logged"
        data = json.loads(logged.strip())
        assert "***" in data["email"], f"Email not masked in: {data}"
        assert "@bar.com" in data["email"]

    def test_dev_mode_skips_masking(self):
        """In debug mode, PII masking processor is NOT in the chain."""
        from src.utils.pii_masking import mask_dict_pii as masker

        output = io.StringIO()
        structlog.configure(
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.JSONRenderer(),
            ],
            logger_factory=structlog.PrintLoggerFactory(file=output),
            cache_logger_on_first_use=False,
        )

        log = structlog.get_logger("test_dev")
        log.info("user_signup", email="foo@bar.com")

        logged = output.getvalue()
        data = json.loads(logged.strip())
        # Without masker in chain, email is unmasked
        assert data["email"] == "foo@bar.com"

    def test_setup_logging_production_includes_masker(self):
        """setup_logging() with debug=False includes mask_dict_pii in the chain."""
        with patch("src.utils.logging.settings") as mock_cfg:
            mock_cfg.debug = False
            mock_cfg.log_level = "INFO"

            from src.utils.logging import setup_logging
            setup_logging()

        config = structlog.get_config()
        processor_names = [p.__name__ if hasattr(p, "__name__") else type(p).__name__
                           for p in config["processors"]]
        assert "mask_dict_pii" in processor_names

    def test_setup_logging_debug_excludes_masker(self):
        """setup_logging() with debug=True does NOT include mask_dict_pii."""
        with patch("src.utils.logging.settings") as mock_cfg:
            mock_cfg.debug = True
            mock_cfg.log_level = "DEBUG"

            from src.utils.logging import setup_logging
            setup_logging()

        config = structlog.get_config()
        processor_names = [p.__name__ if hasattr(p, "__name__") else type(p).__name__
                           for p in config["processors"]]
        assert "mask_dict_pii" not in processor_names
