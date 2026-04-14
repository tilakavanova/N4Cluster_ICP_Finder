"""Tests for NIF-225 / NIF-250: SendGrid Event Webhook endpoint.

Covers:
- POST /webhooks/sendgrid with valid signature processes events
- POST /webhooks/sendgrid with invalid signature returns 401
- POST /webhooks/sendgrid with missing signature headers returns 401
- POST /webhooks/sendgrid with no signing key (dev mode) skips verification
- POST /webhooks/sendgrid queues Celery task
- POST /webhooks/sendgrid returns 200 immediately
- POST /webhooks/sendgrid with invalid JSON returns 400
- POST /webhooks/sendgrid single event dict (not array) is normalised to list
- Broker unavailability still returns 200 (fire-and-forget)
- Event batch processing: delivered, open, click, bounce, spamreport, unsubscribe
- Apple MPP proxy-open detection
- Event deduplication via provider_event_id
- POST /webhooks/sendgrid/inbound for reply detection
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# 7. Missing signature headers — 401
# ---------------------------------------------------------------------------


class TestWebhookMissingSignatureHeaders:
    """When a signing key is configured, absent headers must yield 401.

    verify_sendgrid_signature returns False immediately when signature or
    timestamp are empty strings (the default from .headers.get(..., "")).
    """

    @pytest.mark.asyncio
    async def test_both_headers_missing_returns_401(self):
        payload = json.dumps(SAMPLE_EVENTS).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.settings") as mock_settings:
                mock_settings.sendgrid_webhook_signing_key = VALID_SIGNING_KEY
                # No sig/timestamp headers — real verify_sendgrid_signature returns False
                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_signature_header_missing_returns_401(self):
        """Timestamp present but signature absent → 401."""
        payload = json.dumps(SAMPLE_EVENTS).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.settings") as mock_settings:
                mock_settings.sendgrid_webhook_signing_key = VALID_SIGNING_KEY
                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-twilio-email-event-webhook-timestamp": "1700000000",
                    },
                )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_timestamp_header_missing_returns_401(self):
        """Signature present but timestamp absent → 401."""
        payload = json.dumps(SAMPLE_EVENTS).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.settings") as mock_settings:
                mock_settings.sendgrid_webhook_signing_key = VALID_SIGNING_KEY
                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-twilio-email-event-webhook-signature": "some-sig",
                    },
                )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 8. Event batch processing — all recognized event types
# ---------------------------------------------------------------------------


BATCH_EVENT_TYPES = ["delivered", "open", "click", "bounce", "spamreport", "unsubscribe"]


class TestWebhookEventBatchProcessing:
    """Each recognised SendGrid event type is accepted and forwarded."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type", BATCH_EVENT_TYPES)
    async def test_single_event_type_accepted(self, event_type):
        event = {
            "event": event_type,
            "sg_message_id": f"msg-{event_type}",
            "sg_event_id": f"evt-{event_type}-001",
            "email": "owner@restaurant.com",
            "timestamp": 1700000000,
        }
        payload = json.dumps([event]).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.settings") as mock_settings, \
                 patch("src.api.routers.webhooks.process_sendgrid_events") as mock_task:
                mock_settings.sendgrid_webhook_signing_key = ""
                mock_task.delay = MagicMock()
                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )
        assert resp.status_code == 200
        mock_task.delay.assert_called_once()

    @pytest.mark.asyncio
    async def test_mixed_event_batch_queued_as_single_task(self):
        """All event types can be batched together in a single POST."""
        events = [
            {"event": "delivered", "sg_message_id": "abc", "sg_event_id": "e1",
             "email": "a@x.com", "timestamp": 1700000000},
            {"event": "open", "sg_message_id": "abc", "sg_event_id": "e2",
             "useragent": "Mozilla/5.0", "email": "a@x.com", "timestamp": 1700000001},
            {"event": "click", "sg_message_id": "abc", "sg_event_id": "e3",
             "email": "a@x.com", "timestamp": 1700000002},
            {"event": "bounce", "sg_message_id": "abc", "sg_event_id": "e4",
             "type": "bounce", "email": "a@x.com", "timestamp": 1700000003},
            {"event": "spamreport", "sg_message_id": "abc", "sg_event_id": "e5",
             "email": "a@x.com", "timestamp": 1700000004},
            {"event": "unsubscribe", "sg_message_id": "abc", "sg_event_id": "e6",
             "email": "a@x.com", "timestamp": 1700000005},
        ]
        payload = json.dumps(events).encode()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.settings") as mock_settings, \
                 patch("src.api.routers.webhooks.process_sendgrid_events") as mock_task:
                mock_settings.sendgrid_webhook_signing_key = ""
                mock_task.delay = MagicMock()
                resp = await client.post(
                    "/webhooks/sendgrid",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 200
        mock_task.delay.assert_called_once()
        called_events = mock_task.delay.call_args[0][0]
        assert len(called_events) == 6
        event_types_sent = [e["event"] for e in called_events]
        assert set(event_types_sent) == set(BATCH_EVENT_TYPES)

    @pytest.mark.asyncio
    async def test_event_useragent_preserved_in_payload(self):
        """User-Agent field is forwarded intact to the Celery task."""
        ua = "Mozilla/5.0"
        event = {
            "event": "open",
            "sg_message_id": "abc123",
            "sg_event_id": "evt-ua-001",
            "useragent": ua,
            "email": "a@x.com",
            "timestamp": 1700000000,
        }
        payload = json.dumps([event]).encode()
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

        called_events = mock_task.delay.call_args[0][0]
        assert called_events[0]["useragent"] == ua


# ---------------------------------------------------------------------------
# 9. Apple MPP proxy-open detection
# ---------------------------------------------------------------------------


class TestAppleMppDetection:
    """Unit tests for the _is_apple_mpp() helper in email_tasks."""

    def test_plain_mozilla_ua_is_mpp(self):
        from src.tasks.email_tasks import _is_apple_mpp
        assert _is_apple_mpp("Mozilla/5.0") is True

    def test_mozilla_with_apple_cloudkit_is_mpp(self):
        """Apple MPP proxy UA: Mozilla/5.0 with no real browser version token."""
        from src.tasks.email_tasks import _is_apple_mpp
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"
        assert _is_apple_mpp(ua) is True

    def test_mozilla_with_chrome_is_not_mpp(self):
        from src.tasks.email_tasks import _is_apple_mpp
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        assert _is_apple_mpp(ua) is False

    def test_mozilla_with_safari_is_not_mpp(self):
        from src.tasks.email_tasks import _is_apple_mpp
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/604.1"
        assert _is_apple_mpp(ua) is False

    def test_mozilla_with_firefox_is_not_mpp(self):
        from src.tasks.email_tasks import _is_apple_mpp
        ua = "Mozilla/5.0 (X11; Linux x86_64; rv:78.0) Gecko/20100101 Firefox/78.0"
        assert _is_apple_mpp(ua) is False

    def test_mozilla_with_edge_is_not_mpp(self):
        from src.tasks.email_tasks import _is_apple_mpp
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edg/91.0.864.59"
        assert _is_apple_mpp(ua) is False

    def test_none_ua_is_not_mpp(self):
        from src.tasks.email_tasks import _is_apple_mpp
        assert _is_apple_mpp(None) is False

    def test_empty_ua_is_not_mpp(self):
        from src.tasks.email_tasks import _is_apple_mpp
        assert _is_apple_mpp("") is False

    def test_non_mozilla_ua_is_not_mpp(self):
        """Pure curl/wget UA has no Mozilla token."""
        from src.tasks.email_tasks import _is_apple_mpp
        assert _is_apple_mpp("curl/7.68.0") is False


# ---------------------------------------------------------------------------
# 10. Event deduplication in process_sendgrid_events task
# ---------------------------------------------------------------------------


def _make_dedup_session_factory(*, duplicate: bool = False):
    """Build a mock async_session that returns an existing event on dedup check."""
    dedup_result = MagicMock()
    dedup_result.scalar_one_or_none.return_value = MagicMock() if duplicate else None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=dedup_result)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    session.commit = AsyncMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return lambda: cm, session


def _capture_run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestEventDeduplication:
    def test_duplicate_event_is_not_added_to_session(self):
        """When provider_event_id already exists in DB, session.add is not called."""
        from src.tasks.email_tasks import process_sendgrid_events

        event = {
            "event": "open",
            "sg_message_id": f"msg-{uuid.uuid4()}",
            "sg_event_id": "evt-duplicate-001",
            "email": "owner@restaurant.com",
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        }

        factory, session = _make_dedup_session_factory(duplicate=True)

        with patch("src.tasks.email_tasks.run_async", side_effect=_capture_run_async), \
             patch("src.db.session.async_session", factory):
            result = process_sendgrid_events.run([event])

        session.add.assert_not_called()
        assert result["skipped"] == 1
        assert result["processed"] == 0

    def test_non_duplicate_event_is_added_to_session(self):
        """When provider_event_id is new, session.add is called once."""
        from src.tasks.email_tasks import process_sendgrid_events

        sg_msg_id = f"msg-{uuid.uuid4()}"
        event = {
            "event": "open",
            "sg_message_id": sg_msg_id,
            "sg_event_id": f"evt-new-{uuid.uuid4()}",
            "email": "owner@restaurant.com",
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        }

        # First execute = dedup check (no duplicate), second = activity lookup (no match)
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None

        session = AsyncMock()
        session.execute = AsyncMock(return_value=no_result)
        session.get = AsyncMock(return_value=None)
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.rollback = AsyncMock()
        session.commit = AsyncMock()

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tasks.email_tasks.run_async", side_effect=_capture_run_async), \
             patch("src.db.session.async_session", lambda: cm):
            result = process_sendgrid_events.run([event])

        session.add.assert_called_once()
        assert result["processed"] == 1
        assert result["skipped"] == 0

    def test_same_event_id_in_batch_second_is_skipped(self):
        """Two events sharing the same sg_event_id: first persisted, second skipped."""
        from src.tasks.email_tasks import process_sendgrid_events

        shared_event_id = f"evt-shared-{uuid.uuid4()}"
        events = [
            {
                "event": "open",
                "sg_message_id": "msg-abc",
                "sg_event_id": shared_event_id,
                "email": "a@x.com",
                "timestamp": 1700000000,
            },
            {
                "event": "open",
                "sg_message_id": "msg-abc",
                "sg_event_id": shared_event_id,
                "email": "a@x.com",
                "timestamp": 1700000000,
            },
        ]

        # First event: dedup miss → activity miss → persisted
        # Second event: dedup hit → skipped
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        has_match = MagicMock()
        has_match.scalar_one_or_none.return_value = MagicMock()

        session = AsyncMock()
        # Calls: dedup-1 (miss), activity-1 (miss), dedup-2 (hit)
        session.execute = AsyncMock(side_effect=[no_match, no_match, has_match])
        session.get = AsyncMock(return_value=None)
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.rollback = AsyncMock()
        session.commit = AsyncMock()

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tasks.email_tasks.run_async", side_effect=_capture_run_async), \
             patch("src.db.session.async_session", lambda: cm):
            result = process_sendgrid_events.run(events)

        assert result["processed"] == 1
        assert result["skipped"] == 1


# ---------------------------------------------------------------------------
# 11. POST /webhooks/sendgrid/inbound — reply detection endpoint
# ---------------------------------------------------------------------------


class TestWebhookInboundReplyDetection:
    @pytest.mark.asyncio
    async def test_inbound_returns_200(self):
        """Valid inbound email form data → 200 {"received": true}."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.process_inbound_reply_task") as mock_task:
                mock_task.delay = MagicMock()
                resp = await client.post(
                    "/webhooks/sendgrid/inbound",
                    data={
                        "headers": "In-Reply-To: <msg@sg.net>\r\n",
                        "from": "owner@pasta.com",
                        "subject": "Re: Partnership",
                        "text": "I'm interested",
                        "to": "outreach@n4cluster.com",
                        "html": "",
                        "envelope": "{}",
                    },
                )
        assert resp.status_code == 200
        assert resp.json() == {"received": True}

    @pytest.mark.asyncio
    async def test_inbound_queues_task_with_form_data(self):
        """Form fields from the inbound email are forwarded to the Celery task."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.process_inbound_reply_task") as mock_task:
                mock_task.delay = MagicMock()
                await client.post(
                    "/webhooks/sendgrid/inbound",
                    data={
                        "headers": "In-Reply-To: <original@sg.net>\r\n",
                        "from": "chef@bistro.com",
                        "subject": "Re: Outreach email",
                        "text": "Sure, let's talk",
                        "to": "sales@n4cluster.com",
                        "html": "<p>Sure</p>",
                        "envelope": '{"from":"chef@bistro.com"}',
                    },
                )
        mock_task.delay.assert_called_once()
        inbound = mock_task.delay.call_args[0][0]
        assert inbound["from"] == "chef@bistro.com"
        assert inbound["subject"] == "Re: Outreach email"
        assert inbound["headers"] == "In-Reply-To: <original@sg.net>\r\n"
        assert inbound["text"] == "Sure, let's talk"

    @pytest.mark.asyncio
    async def test_inbound_missing_from_field_still_returns_200(self):
        """Inbound endpoint is tolerant — missing fields default to empty string."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.process_inbound_reply_task") as mock_task:
                mock_task.delay = MagicMock()
                resp = await client.post(
                    "/webhooks/sendgrid/inbound",
                    data={
                        "headers": "",
                        "subject": "Hello",
                        "text": "Body",
                    },
                )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_inbound_broker_unavailable_still_returns_200(self):
        """Broker failure on inbound endpoint must not surface as an error."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.api.routers.webhooks.process_inbound_reply_task") as mock_task:
                mock_task.delay.side_effect = Exception("broker down")
                resp = await client.post(
                    "/webhooks/sendgrid/inbound",
                    data={
                        "headers": "In-Reply-To: <x@sg.net>\r\n",
                        "from": "a@b.com",
                        "subject": "Re: test",
                        "text": "reply",
                    },
                )
        assert resp.status_code == 200
