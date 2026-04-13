"""Tests for NIF-225: SendGrid Event Webhook endpoint.

Covers:
- POST /webhooks/sendgrid with valid signature processes events
- POST /webhooks/sendgrid with invalid signature returns 401
- POST /webhooks/sendgrid with no signing key (dev mode) skips verification
- POST /webhooks/sendgrid queues Celery task
- POST /webhooks/sendgrid returns 200 immediately
- POST /webhooks/sendgrid with invalid JSON returns 400
- POST /webhooks/sendgrid single event dict (not array) is normalised to list
- Broker unavailability still returns 200 (fire-and-forget)
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_EVENTS = [
    {
        "event": "delivered",
        "sg_message_id": "abc123",
        "sg_event_id": "evt-001",
        "email": "owner@restaurant.com",
        "timestamp": 1700000000,
    },
    {
        "event": "open",
        "sg_message_id": "abc123",
        "sg_event_id": "evt-002",
        "email": "owner@restaurant.com",
        "timestamp": 1700000060,
        "useragent": "Mozilla/5.0 (iPhone; CPU iPhone OS) AppleWebKit",
    },
]

VALID_SIGNING_KEY = "test-signing-key"


def _make_valid_sig_headers(timestamp: str = "1700000000"):
    return {
        "x-twilio-email-event-webhook-signature": "valid-sig",
        "x-twilio-email-event-webhook-timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# 1. Valid signature — happy path
# ---------------------------------------------------------------------------


class TestWebhookValidSignature:
    @pytest.mark.asyncio
    async def test_returns_200_on_valid_signature(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.verify_sendgrid_signature", return_value=True), \
                 patch("src.api.routers.webhooks.settings") as mock_settings, \
                 patch("src.tasks.email_tasks.process_sendgrid_events") as mock_task:
                mock_settings.sendgrid_webhook_signing_key = VALID_SIGNING_KEY
                mock_task.delay = MagicMock()

                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={
                        "Content-Type": "application/json",
                        **_make_valid_sig_headers(),
                    },
                )

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_queues_celery_task_with_events(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.verify_sendgrid_signature", return_value=True), \
                 patch("src.api.routers.webhooks.settings") as mock_settings, \
                 patch("src.api.routers.webhooks.process_sendgrid_events") as mock_task:
                mock_settings.sendgrid_webhook_signing_key = VALID_SIGNING_KEY
                mock_task.delay = MagicMock()

                await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={"Content-Type": "application/json", **_make_valid_sig_headers()},
                )

        mock_task.delay.assert_called_once_with(SAMPLE_EVENTS)

    @pytest.mark.asyncio
    async def test_response_body_contains_received_true(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.verify_sendgrid_signature", return_value=True), \
                 patch("src.api.routers.webhooks.settings") as mock_settings, \
                 patch("src.api.routers.webhooks.process_sendgrid_events") as mock_task:
                mock_settings.sendgrid_webhook_signing_key = VALID_SIGNING_KEY
                mock_task.delay = MagicMock()

                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={"Content-Type": "application/json", **_make_valid_sig_headers()},
                )

        body = resp.json()
        assert body.get("received") is True


# ---------------------------------------------------------------------------
# 2. Invalid signature — 401
# ---------------------------------------------------------------------------


class TestWebhookInvalidSignature:
    @pytest.mark.asyncio
    async def test_invalid_signature_returns_401(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.verify_sendgrid_signature", return_value=False), \
                 patch("src.api.routers.webhooks.settings") as mock_settings:
                mock_settings.sendgrid_webhook_signing_key = VALID_SIGNING_KEY

                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-twilio-email-event-webhook-signature": "bad-sig",
                        "x-twilio-email-event-webhook-timestamp": "1700000000",
                    },
                )

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_signature_does_not_queue_task(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.verify_sendgrid_signature", return_value=False), \
                 patch("src.api.routers.webhooks.settings") as mock_settings, \
                 patch("src.api.routers.webhooks.process_sendgrid_events") as mock_task:
                mock_settings.sendgrid_webhook_signing_key = VALID_SIGNING_KEY
                mock_task.delay = MagicMock()

                await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-twilio-email-event-webhook-signature": "bad-sig",
                        "x-twilio-email-event-webhook-timestamp": "1700000000",
                    },
                )

        mock_task.delay.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Dev mode — no signing key skips verification
# ---------------------------------------------------------------------------


class TestWebhookDevMode:
    @pytest.mark.asyncio
    async def test_empty_signing_key_skips_verification(self):
        """When sendgrid_webhook_signing_key is empty, accept without checking signature."""
        payload = json.dumps(SAMPLE_EVENTS).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.verify_sendgrid_signature") as mock_verify, \
                 patch("src.api.routers.webhooks.settings") as mock_settings, \
                 patch("src.api.routers.webhooks.process_sendgrid_events") as mock_task:
                mock_settings.sendgrid_webhook_signing_key = ""
                mock_task.delay = MagicMock()

                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )

        mock_verify.assert_not_called()
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. Invalid JSON payload
# ---------------------------------------------------------------------------


class TestWebhookInvalidPayload:
    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.settings") as mock_settings:
                mock_settings.sendgrid_webhook_signing_key = ""

                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=b"not-valid-json{{",
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 5. Single event dict normalised to list
# ---------------------------------------------------------------------------


class TestWebhookSingleEvent:
    @pytest.mark.asyncio
    async def test_single_event_dict_normalised_to_list(self):
        single_event = SAMPLE_EVENTS[0]
        payload = json.dumps(single_event).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.settings") as mock_settings, \
                 patch("src.api.routers.webhooks.process_sendgrid_events") as mock_task:
                mock_settings.sendgrid_webhook_signing_key = ""
                mock_task.delay = MagicMock()

                await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )

        # Should be called with a list containing the single event
        mock_task.delay.assert_called_once()
        called_events = mock_task.delay.call_args[0][0]
        assert isinstance(called_events, list)
        assert len(called_events) == 1


# ---------------------------------------------------------------------------
# 6. Broker unavailability — still returns 200
# ---------------------------------------------------------------------------


class TestWebhookBrokerFailure:
    @pytest.mark.asyncio
    async def test_broker_error_still_returns_200(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.settings") as mock_settings, \
                 patch("src.api.routers.webhooks.process_sendgrid_events") as mock_task:
                mock_settings.sendgrid_webhook_signing_key = ""
                mock_task.delay.side_effect = Exception("broker unavailable")

                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 200
