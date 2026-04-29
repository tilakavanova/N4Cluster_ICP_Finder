"""Tests for SMS send service via Plivo (NIF-231).

Covers:
- send_sms success path with mocked Plivo API
- send_sms blocked by TCPA (no consent)
- send_sms blocked by TCPA (quiet hours)
- send_sms Plivo API error handling
- send_bulk_sms with mixed results
- send_bulk_sms rate limiting
- handle_delivery_callback processes Plivo webhook
- handle_delivery_callback deduplicates
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.sms import SMSService, handle_delivery_callback


def _uuid():
    return uuid.uuid4()


class TestSendSms:
    @pytest.mark.asyncio
    async def test_send_success(self):
        """Successful SMS send via Plivo."""
        session = AsyncMock()
        lead_id = _uuid()
        campaign_id = _uuid()
        target_id = _uuid()
        msg_uuid = "plivo-msg-uuid-123"

        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "message": "message(s) queued",
            "message_uuid": [msg_uuid],
            "api_id": "api-id-123",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("src.services.sms.can_send_sms", return_value=(True, None)), \
             patch("src.services.sms.replace_urls_in_message", return_value="Hello!"), \
             patch("src.services.sms.httpx.AsyncClient") as mock_client_cls, \
             patch("src.services.sms.log_activity") as mock_log_activity, \
             patch("src.services.sms.mark_as_sent") as mock_mark_sent:

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            mock_activity = MagicMock()
            mock_log_activity.return_value = mock_activity

            svc = SMSService(auth_id="test-id", auth_token="test-token", from_number="+15551234567")
            result = await svc.send_sms(
                session=session,
                to_number="+14155551234",
                message="Hello from N4Cluster!",
                lead_id=lead_id,
                campaign_id=campaign_id,
                target_id=target_id,
            )

        assert result["status"] == "sent"
        assert result["message_uuid"] == msg_uuid
        assert result["error"] is None
        session.add.assert_called_once()  # TrackerEvent added
        mock_mark_sent.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_blocked_no_consent(self):
        """SMS blocked when recipient has no consent."""
        session = AsyncMock()

        with patch("src.services.sms.can_send_sms", return_value=(False, "no_consent")), \
             patch("src.services.sms.mark_as_opted_out") as mock_opted_out:

            svc = SMSService(auth_id="test-id", auth_token="test-token")
            result = await svc.send_sms(
                session=session,
                to_number="+14155551234",
                message="Hello!",
                lead_id=_uuid(),
                campaign_id=_uuid(),
                target_id=_uuid(),
            )

        assert result["status"] == "blocked"
        assert result["error"] == "no_consent"
        mock_opted_out.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_blocked_quiet_hours(self):
        """SMS blocked during quiet hours."""
        session = AsyncMock()

        with patch("src.services.sms.can_send_sms", return_value=(False, "quiet_hours")):
            svc = SMSService(auth_id="test-id", auth_token="test-token")
            result = await svc.send_sms(
                session=session,
                to_number="+14155551234",
                message="Hello!",
                lead_id=_uuid(),
                campaign_id=_uuid(),
                timezone_str="US/Eastern",
            )

        assert result["status"] == "blocked"
        assert result["error"] == "quiet_hours"

    @pytest.mark.asyncio
    async def test_send_plivo_api_error(self):
        """Plivo API returns an error."""
        import httpx as httpx_lib

        session = AsyncMock()
        target_id = _uuid()

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_response.raise_for_status.side_effect = httpx_lib.HTTPStatusError(
            "400 Bad Request", request=MagicMock(), response=mock_response
        )

        with patch("src.services.sms.can_send_sms", return_value=(True, None)), \
             patch("src.services.sms.replace_urls_in_message", return_value="Hello!"), \
             patch("src.services.sms.httpx.AsyncClient") as mock_client_cls, \
             patch("src.services.sms.mark_as_failed") as mock_mark_failed:

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            svc = SMSService(auth_id="test-id", auth_token="test-token", from_number="+15551234567")
            result = await svc.send_sms(
                session=session,
                to_number="+14155551234",
                message="Hello!",
                lead_id=_uuid(),
                campaign_id=_uuid(),
                target_id=target_id,
            )

        assert result["status"] == "failed"
        assert "Plivo API error" in result["error"]
        mock_mark_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_sms_string_message_uuid(self):
        """Plivo returns message_uuid as string (not list)."""
        session = AsyncMock()

        mock_response = MagicMock()
        mock_response.json.return_value = {"message_uuid": "single-uuid"}
        mock_response.raise_for_status = MagicMock()

        with patch("src.services.sms.can_send_sms", return_value=(True, None)), \
             patch("src.services.sms.replace_urls_in_message", return_value="Hi"), \
             patch("src.services.sms.httpx.AsyncClient") as mock_client_cls, \
             patch("src.services.sms.log_activity") as mock_log, \
             patch("src.services.sms.mark_as_sent"):

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            mock_log.return_value = MagicMock()

            svc = SMSService(auth_id="id", auth_token="tok", from_number="+15551234567")
            result = await svc.send_sms(
                session=session,
                to_number="+14155551234",
                message="Hi",
                lead_id=_uuid(),
                campaign_id=_uuid(),
                target_id=_uuid(),
            )

        assert result["message_uuid"] == "single-uuid"


class TestSendBulkSms:
    @pytest.mark.asyncio
    async def test_bulk_mixed_results(self):
        """Bulk send with a mix of sent and blocked results."""
        session = AsyncMock()
        campaign_id = _uuid()

        call_count = 0

        async def mock_send(session, to_number, message, lead_id, campaign_id, target_id, timezone_str=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"status": "sent", "message_uuid": "uuid-1", "error": None}
            else:
                return {"status": "blocked", "message_uuid": None, "error": "no_consent"}

        svc = SMSService(auth_id="id", auth_token="tok")
        svc.send_sms = mock_send

        targets = [
            {"target_id": str(_uuid()), "lead_id": str(_uuid()), "phone_number": "+14155551234"},
            {"target_id": str(_uuid()), "lead_id": str(_uuid()), "phone_number": "+14155555678"},
        ]

        result = await svc.send_bulk_sms(session, campaign_id, "Hello {{name}}!", targets)

        assert result["sent"] == 1
        assert result["blocked"] == 1
        assert len(result["details"]) == 2

    @pytest.mark.asyncio
    async def test_bulk_rate_limiting(self):
        """Bulk send respects daily SMS limit."""
        session = AsyncMock()
        campaign_id = _uuid()

        async def mock_send(session, to_number, message, lead_id, campaign_id, target_id, timezone_str=None):
            return {"status": "sent", "message_uuid": "uuid", "error": None}

        svc = SMSService(auth_id="id", auth_token="tok")
        svc.send_sms = mock_send

        # Create 105 targets (limit is 100)
        targets = [
            {"target_id": str(_uuid()), "lead_id": str(_uuid()), "phone_number": f"+1415555{i:04d}"}
            for i in range(105)
        ]

        result = await svc.send_bulk_sms(session, campaign_id, "Hello!", targets)

        assert result["sent"] == 100
        assert result["rate_limited"] == 5


class TestDeliveryCallback:
    @pytest.mark.asyncio
    async def test_processes_delivered_status(self):
        session = AsyncMock()

        callback_data = {
            "MessageUUID": "plivo-uuid-123",
            "Status": "delivered",
            "From": "+15551234567",
            "To": "+14155551234",
        }

        await handle_delivery_callback(session, callback_data)

        session.add.assert_called_once()
        added_event = session.add.call_args[0][0]
        assert added_event.event_type == "delivery"
        assert added_event.channel == "sms"
        assert added_event.provider == "plivo"
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_processes_failed_status(self):
        session = AsyncMock()

        callback_data = {
            "MessageUUID": "plivo-uuid-456",
            "Status": "failed",
        }

        await handle_delivery_callback(session, callback_data)

        added_event = session.add.call_args[0][0]
        assert added_event.event_type == "bounce"

    @pytest.mark.asyncio
    async def test_ignores_missing_uuid(self):
        session = AsyncMock()
        await handle_delivery_callback(session, {})
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_deduplicates_callback(self):
        session = AsyncMock()
        session.commit.side_effect = Exception("UNIQUE constraint failed")

        callback_data = {
            "MessageUUID": "plivo-uuid-789",
            "Status": "delivered",
        }

        # Should not raise
        await handle_delivery_callback(session, callback_data)
        session.rollback.assert_called_once()
