"""Tests for NIF-257: HubSpot bidirectional webhook endpoint.

Covers:
- POST /webhooks/hubspot with valid signature returns 200
- POST /webhooks/hubspot with invalid signature returns 401
- POST /webhooks/hubspot with no secret (dev mode) skips verification
- POST /webhooks/hubspot queues Celery task with events
- POST /webhooks/hubspot broker failure still returns 200
- POST /webhooks/hubspot with invalid JSON returns 400
- POST /webhooks/hubspot single dict normalised to list
- POST /webhooks/hubspot empty events array returns 200
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

DEAL_STAGE_EVENT = {
    "subscriptionType": "deal.propertyChange",
    "objectId": 12345,
    "propertyName": "dealstage",
    "propertyValue": "closedwon",
    "changeSource": "CRM",
    "eventId": 1,
    "appId": 98765,
    "occurredAt": 1700000000000,
    "attemptNumber": 0,
}

CONTACT_PROPERTY_EVENT = {
    "subscriptionType": "contact.propertyChange",
    "objectId": 67890,
    "propertyName": "firstname",
    "propertyValue": "Alice",
    "changeSource": "CRM",
    "eventId": 2,
    "appId": 98765,
    "occurredAt": 1700000001000,
    "attemptNumber": 0,
}

SAMPLE_EVENTS = [DEAL_STAGE_EVENT, CONTACT_PROPERTY_EVENT]

VALID_SECRET = "test-hubspot-client-secret"


def _valid_sig_headers() -> dict:
    return {
        "x-hubspot-signature-v3": "valid-sig",
        "x-hubspot-request-timestamp": "1700000000000",
    }


# ---------------------------------------------------------------------------
# 1. Valid signature — happy path
# ---------------------------------------------------------------------------


class TestHubSpotWebhookValidSignature:
    @pytest.mark.asyncio
    async def test_returns_200_on_valid_signature(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with (
                patch("src.api.routers.hubspot_webhooks.verify_hubspot_signature", return_value=True),
                patch("src.api.routers.hubspot_webhooks.settings") as mock_settings,
                patch("src.api.routers.hubspot_webhooks.process_hubspot_webhook") as mock_task,
            ):
                mock_settings.hubspot_webhook_secret = VALID_SECRET
                mock_task.delay = MagicMock()

                resp = await client.post(
                    "/webhooks/hubspot",
                    content=payload,
                    headers={"Content-Type": "application/json", **_valid_sig_headers()},
                )

        assert resp.status_code == 200
        assert resp.json() == {"received": True}

    @pytest.mark.asyncio
    async def test_queues_celery_task_with_events(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with (
                patch("src.api.routers.hubspot_webhooks.verify_hubspot_signature", return_value=True),
                patch("src.api.routers.hubspot_webhooks.settings") as mock_settings,
                patch("src.api.routers.hubspot_webhooks.process_hubspot_webhook") as mock_task,
            ):
                mock_settings.hubspot_webhook_secret = VALID_SECRET
                mock_delay = MagicMock()
                mock_task.delay = mock_delay

                await client.post(
                    "/webhooks/hubspot",
                    content=payload,
                    headers={"Content-Type": "application/json", **_valid_sig_headers()},
                )

        mock_delay.assert_called_once_with(SAMPLE_EVENTS)


# ---------------------------------------------------------------------------
# 2. Invalid signature — 401
# ---------------------------------------------------------------------------


class TestHubSpotWebhookInvalidSignature:
    @pytest.mark.asyncio
    async def test_returns_401_on_invalid_signature(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with (
                patch("src.api.routers.hubspot_webhooks.verify_hubspot_signature", return_value=False),
                patch("src.api.routers.hubspot_webhooks.settings") as mock_settings,
            ):
                mock_settings.hubspot_webhook_secret = VALID_SECRET

                resp = await client.post(
                    "/webhooks/hubspot",
                    content=payload,
                    headers={"Content-Type": "application/json", **_valid_sig_headers()},
                )

        assert resp.status_code == 401
        assert "Invalid webhook signature" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_does_not_queue_task_on_invalid_signature(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with (
                patch("src.api.routers.hubspot_webhooks.verify_hubspot_signature", return_value=False),
                patch("src.api.routers.hubspot_webhooks.settings") as mock_settings,
                patch("src.api.routers.hubspot_webhooks.process_hubspot_webhook") as mock_task,
            ):
                mock_settings.hubspot_webhook_secret = VALID_SECRET
                mock_delay = MagicMock()
                mock_task.delay = mock_delay

                await client.post(
                    "/webhooks/hubspot",
                    content=payload,
                    headers={"Content-Type": "application/json", **_valid_sig_headers()},
                )

        mock_delay.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Dev mode — no secret configured → skip verification
# ---------------------------------------------------------------------------


class TestHubSpotWebhookDevMode:
    @pytest.mark.asyncio
    async def test_no_secret_skips_verification_and_returns_200(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with (
                patch("src.api.routers.hubspot_webhooks.settings") as mock_settings,
                patch("src.api.routers.hubspot_webhooks.process_hubspot_webhook") as mock_task,
            ):
                mock_settings.hubspot_webhook_secret = ""  # no secret = dev mode
                mock_task.delay = MagicMock()

                resp = await client.post(
                    "/webhooks/hubspot",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. Bad JSON — 400
# ---------------------------------------------------------------------------


class TestHubSpotWebhookBadJson:
    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.hubspot_webhooks.settings") as mock_settings:
                mock_settings.hubspot_webhook_secret = ""

                resp = await client.post(
                    "/webhooks/hubspot",
                    content=b"not-json",
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 5. Single dict normalised to list
# ---------------------------------------------------------------------------


class TestHubSpotWebhookSingleEvent:
    @pytest.mark.asyncio
    async def test_single_dict_normalised_to_list(self):
        payload = json.dumps(DEAL_STAGE_EVENT).encode()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with (
                patch("src.api.routers.hubspot_webhooks.settings") as mock_settings,
                patch("src.api.routers.hubspot_webhooks.process_hubspot_webhook") as mock_task,
            ):
                mock_settings.hubspot_webhook_secret = ""
                mock_delay = MagicMock()
                mock_task.delay = mock_delay

                resp = await client.post(
                    "/webhooks/hubspot",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 200
        # Task was called with a list of one event
        mock_delay.assert_called_once_with([DEAL_STAGE_EVENT])


# ---------------------------------------------------------------------------
# 6. Broker failure — still returns 200
# ---------------------------------------------------------------------------


class TestHubSpotWebhookBrokerFailure:
    @pytest.mark.asyncio
    async def test_broker_failure_still_returns_200(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with (
                patch("src.api.routers.hubspot_webhooks.settings") as mock_settings,
                patch("src.api.routers.hubspot_webhooks.process_hubspot_webhook") as mock_task,
            ):
                mock_settings.hubspot_webhook_secret = ""
                mock_task.delay = MagicMock(side_effect=ConnectionError("Redis down"))

                resp = await client.post(
                    "/webhooks/hubspot",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 200
