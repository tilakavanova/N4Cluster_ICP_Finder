"""Tests for NIF-226: Email send service with tracking.

Covers:
- send_outreach_email happy path: email sent, status SENT, activity has message_id
- send_outreach_email with opted-out lead: no email sent, status OPTED_OUT
- send_outreach_email with SendGrid failure: status FAILED
- send_outreach_email with missing lead: status FAILED
- send_outreach_email with missing email address: status FAILED
- send_outreach_email with missing target: status FAILED
- send_bulk_outreach sends to multiple targets and returns correct summary
- send_bulk_outreach rate limiting stops at DAILY_SEND_LIMIT
- send_bulk_outreach personalisation template substitution
- send_bulk_outreach partial failure / mixed results
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.services.email_service import send_bulk_outreach, send_outreach_email, DAILY_SEND_LIMIT


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_lead(
    lead_id=None,
    email="owner@restaurant.com",
    email_opt_out=False,
):
    lead = MagicMock()
    lead.id = lead_id or uuid.uuid4()
    lead.email = email
    lead.email_opt_out = email_opt_out
    return lead


def _make_target(target_id=None, lead_id=None, campaign_id=None):
    target = MagicMock()
    target.id = target_id or uuid.uuid4()
    target.lead_id = lead_id or uuid.uuid4()
    target.campaign_id = campaign_id or uuid.uuid4()
    target.communication_status = "queued"
    return target


def _make_activity(target_id=None):
    activity = MagicMock()
    activity.id = uuid.uuid4()
    activity.target_id = target_id or uuid.uuid4()
    activity.external_message_id = None
    activity.channel = None
    return activity


def _make_session(lead=None, target=None):
    """Build a minimal AsyncSession mock."""
    session = AsyncMock()
    session.commit = AsyncMock()

    # session.get returns lead on first call, target on second
    get_returns = []
    if lead is not None:
        get_returns.append(lead)
    if target is not None:
        get_returns.append(target)

    session.get = AsyncMock(side_effect=get_returns)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


# ── send_outreach_email ───────────────────────────────────────────────────────


class TestSendOutreachEmailHappyPath:
    @pytest.mark.asyncio
    async def test_returns_sent_status(self):
        lead = _make_lead()
        target = _make_target(lead_id=lead.id)
        activity = _make_activity(target_id=target.id)
        session = _make_session(lead=lead, target=target)

        with patch("src.services.email_service._build_sendgrid_client") as mock_build, \
             patch("src.services.email_service.mark_as_sent", new_callable=AsyncMock) as mock_sent, \
             patch("src.services.email_service.log_activity", new_callable=AsyncMock, return_value=activity):
            mock_client = MagicMock()
            mock_client.send_email.return_value = (True, "msg-123", None)
            mock_build.return_value = mock_client

            result = await send_outreach_email(
                session=session,
                target_id=target.id,
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="Hello",
                html_content="<p>Hi</p>",
            )

        assert result["status"] == "sent"
        assert result["message_id"] == "msg-123"
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_external_message_id_stored_on_activity(self):
        lead = _make_lead()
        target = _make_target(lead_id=lead.id)
        activity = _make_activity(target_id=target.id)
        session = _make_session(lead=lead, target=target)

        with patch("src.services.email_service._build_sendgrid_client") as mock_build, \
             patch("src.services.email_service.mark_as_sent", new_callable=AsyncMock), \
             patch("src.services.email_service.log_activity", new_callable=AsyncMock, return_value=activity):
            mock_client = MagicMock()
            mock_client.send_email.return_value = (True, "ext-msg-456", None)
            mock_build.return_value = mock_client

            await send_outreach_email(
                session=session,
                target_id=target.id,
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="S",
                html_content="<p>Body</p>",
            )

        assert activity.external_message_id == "ext-msg-456"
        assert activity.channel == "email"

    @pytest.mark.asyncio
    async def test_mark_as_sent_called_with_message_id(self):
        lead = _make_lead()
        target = _make_target(lead_id=lead.id)
        activity = _make_activity(target_id=target.id)
        session = _make_session(lead=lead, target=target)

        with patch("src.services.email_service._build_sendgrid_client") as mock_build, \
             patch("src.services.email_service.mark_as_sent", new_callable=AsyncMock) as mock_sent, \
             patch("src.services.email_service.log_activity", new_callable=AsyncMock, return_value=activity):
            mock_client = MagicMock()
            mock_client.send_email.return_value = (True, "msg-789", None)
            mock_build.return_value = mock_client

            await send_outreach_email(
                session=session,
                target_id=target.id,
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="S",
                html_content="<p>X</p>",
            )

        mock_sent.assert_called_once_with(
            session, target.id, "email", external_message_id="msg-789"
        )

    @pytest.mark.asyncio
    async def test_session_committed_on_success(self):
        lead = _make_lead()
        target = _make_target(lead_id=lead.id)
        activity = _make_activity()
        session = _make_session(lead=lead, target=target)

        with patch("src.services.email_service._build_sendgrid_client") as mock_build, \
             patch("src.services.email_service.mark_as_sent", new_callable=AsyncMock), \
             patch("src.services.email_service.log_activity", new_callable=AsyncMock, return_value=activity):
            mock_client = MagicMock()
            mock_client.send_email.return_value = (True, "msg-x", None)
            mock_build.return_value = mock_client

            await send_outreach_email(
                session=session,
                target_id=target.id,
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="S",
                html_content="<p>X</p>",
            )

        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_tracking_data_passed_to_sendgrid(self):
        lead = _make_lead()
        target = _make_target(lead_id=lead.id)
        activity = _make_activity()
        session = _make_session(lead=lead, target=target)
        campaign_id = uuid.uuid4()

        with patch("src.services.email_service._build_sendgrid_client") as mock_build, \
             patch("src.services.email_service.mark_as_sent", new_callable=AsyncMock), \
             patch("src.services.email_service.log_activity", new_callable=AsyncMock, return_value=activity):
            mock_client = MagicMock()
            mock_client.send_email.return_value = (True, "m", None)
            mock_build.return_value = mock_client

            await send_outreach_email(
                session=session,
                target_id=target.id,
                lead_id=lead.id,
                campaign_id=campaign_id,
                subject="Subject",
                html_content="<p>Hi</p>",
            )

        call_kwargs = mock_client.send_email.call_args[1]
        td = call_kwargs["tracking_data"]
        assert td["lead_id"] == str(lead.id)
        assert td["campaign_id"] == str(campaign_id)
        assert td["target_id"] == str(target.id)


class TestSendOutreachEmailOptedOut:
    @pytest.mark.asyncio
    async def test_returns_opted_out_status(self):
        lead = _make_lead(email_opt_out=True)
        session = _make_session(lead=lead)

        with patch("src.services.email_service.mark_as_opted_out", new_callable=AsyncMock) as mock_opt:
            result = await send_outreach_email(
                session=session,
                target_id=uuid.uuid4(),
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="S",
                html_content="<p>X</p>",
            )

        assert result["status"] == "opted_out"
        assert result["message_id"] is None
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_no_email_sent_when_opted_out(self):
        lead = _make_lead(email_opt_out=True)
        session = _make_session(lead=lead)

        with patch("src.services.email_service.mark_as_opted_out", new_callable=AsyncMock), \
             patch("src.services.email_service._build_sendgrid_client") as mock_build:
            await send_outreach_email(
                session=session,
                target_id=uuid.uuid4(),
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="S",
                html_content="<p>X</p>",
            )

        mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_mark_as_opted_out_called(self):
        lead = _make_lead(email_opt_out=True)
        target_id = uuid.uuid4()
        session = _make_session(lead=lead)

        with patch("src.services.email_service.mark_as_opted_out", new_callable=AsyncMock) as mock_opt:
            await send_outreach_email(
                session=session,
                target_id=target_id,
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="S",
                html_content="<p>X</p>",
            )

        mock_opt.assert_called_once_with(session, target_id, "email")


class TestSendOutreachEmailFailure:
    @pytest.mark.asyncio
    async def test_sendgrid_failure_returns_failed_status(self):
        lead = _make_lead()
        target = _make_target(lead_id=lead.id)
        session = _make_session(lead=lead, target=target)

        with patch("src.services.email_service._build_sendgrid_client") as mock_build, \
             patch("src.services.email_service.mark_as_failed", new_callable=AsyncMock):
            mock_client = MagicMock()
            mock_client.send_email.return_value = (False, None, "rate limit exceeded")
            mock_build.return_value = mock_client

            result = await send_outreach_email(
                session=session,
                target_id=target.id,
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="S",
                html_content="<p>X</p>",
            )

        assert result["status"] == "failed"
        assert result["message_id"] is None
        assert "rate limit" in result["error"]

    @pytest.mark.asyncio
    async def test_mark_as_failed_called_on_sendgrid_failure(self):
        lead = _make_lead()
        target = _make_target(lead_id=lead.id)
        session = _make_session(lead=lead, target=target)

        with patch("src.services.email_service._build_sendgrid_client") as mock_build, \
             patch("src.services.email_service.mark_as_failed", new_callable=AsyncMock) as mock_fail:
            mock_client = MagicMock()
            mock_client.send_email.return_value = (False, None, "API error")
            mock_build.return_value = mock_client

            await send_outreach_email(
                session=session,
                target_id=target.id,
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="S",
                html_content="<p>X</p>",
            )

        mock_fail.assert_called_once_with(session, target.id, "email", error_reason="API error")

    @pytest.mark.asyncio
    async def test_missing_lead_returns_failed(self):
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        session.commit = AsyncMock()

        result = await send_outreach_email(
            session=session,
            target_id=uuid.uuid4(),
            lead_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            subject="S",
            html_content="<p>X</p>",
        )

        assert result["status"] == "failed"
        assert "Lead not found" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_target_returns_failed(self):
        lead = _make_lead()
        # get: first call returns lead, second returns None (target missing)
        session = AsyncMock()
        session.get = AsyncMock(side_effect=[lead, None])
        session.commit = AsyncMock()

        with patch("src.services.email_service.mark_as_failed", new_callable=AsyncMock):
            result = await send_outreach_email(
                session=session,
                target_id=uuid.uuid4(),
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="S",
                html_content="<p>X</p>",
            )

        assert result["status"] == "failed"
        assert "Target not found" in result["error"]

    @pytest.mark.asyncio
    async def test_no_email_address_returns_failed(self):
        lead = _make_lead(email=None)
        target = _make_target(lead_id=lead.id)
        session = _make_session(lead=lead, target=target)

        with patch("src.services.email_service.mark_as_failed", new_callable=AsyncMock) as mock_fail:
            result = await send_outreach_email(
                session=session,
                target_id=target.id,
                lead_id=lead.id,
                campaign_id=uuid.uuid4(),
                subject="S",
                html_content="<p>X</p>",
            )

        assert result["status"] == "failed"
        assert "no email address" in result["error"]
        mock_fail.assert_called_once()


# ── send_bulk_outreach ────────────────────────────────────────────────────────


def _make_target_dict(target_id=None, lead_id=None):
    return {
        "target_id": target_id or uuid.uuid4(),
        "lead_id": lead_id or uuid.uuid4(),
        "email": "test@example.com",
        "personalization_data": {},
    }


class TestSendBulkOutreach:
    @pytest.mark.asyncio
    async def test_returns_correct_sent_count(self):
        targets = [_make_target_dict() for _ in range(3)]
        campaign_id = uuid.uuid4()

        with patch("src.services.email_service.send_outreach_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": "sent", "message_id": "m1", "error": None}
            result = await send_bulk_outreach(
                session=AsyncMock(),
                campaign_id=campaign_id,
                subject="Hello",
                html_template="<p>Hi</p>",
                targets=targets,
            )

        assert result["sent"] == 3
        assert result["failed"] == 0
        assert result["opted_out"] == 0

    @pytest.mark.asyncio
    async def test_returns_correct_failed_count(self):
        targets = [_make_target_dict() for _ in range(2)]

        with patch("src.services.email_service.send_outreach_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": "failed", "message_id": None, "error": "error"}
            result = await send_bulk_outreach(
                session=AsyncMock(),
                campaign_id=uuid.uuid4(),
                subject="S",
                html_template="<p>Hi</p>",
                targets=targets,
            )

        assert result["failed"] == 2
        assert result["sent"] == 0

    @pytest.mark.asyncio
    async def test_returns_correct_opted_out_count(self):
        targets = [_make_target_dict() for _ in range(2)]

        with patch("src.services.email_service.send_outreach_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": "opted_out", "message_id": None, "error": None}
            result = await send_bulk_outreach(
                session=AsyncMock(),
                campaign_id=uuid.uuid4(),
                subject="S",
                html_template="<p>Hi</p>",
                targets=targets,
            )

        assert result["opted_out"] == 2
        assert result["sent"] == 0

    @pytest.mark.asyncio
    async def test_details_list_has_entry_per_target(self):
        targets = [_make_target_dict() for _ in range(4)]

        with patch("src.services.email_service.send_outreach_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": "sent", "message_id": "m", "error": None}
            result = await send_bulk_outreach(
                session=AsyncMock(),
                campaign_id=uuid.uuid4(),
                subject="S",
                html_template="<p>Hi</p>",
                targets=targets,
            )

        assert len(result["details"]) == 4

    @pytest.mark.asyncio
    async def test_mixed_results_summary(self):
        targets = [_make_target_dict() for _ in range(4)]
        statuses = [
            {"status": "sent", "message_id": "m1", "error": None},
            {"status": "failed", "message_id": None, "error": "err"},
            {"status": "opted_out", "message_id": None, "error": None},
            {"status": "sent", "message_id": "m2", "error": None},
        ]

        with patch("src.services.email_service.send_outreach_email", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = statuses
            result = await send_bulk_outreach(
                session=AsyncMock(),
                campaign_id=uuid.uuid4(),
                subject="S",
                html_template="<p>Hi</p>",
                targets=targets,
            )

        assert result["sent"] == 2
        assert result["failed"] == 1
        assert result["opted_out"] == 1

    @pytest.mark.asyncio
    async def test_rate_limit_stops_at_daily_send_limit(self):
        """Targets beyond DAILY_SEND_LIMIT should be marked rate_limited."""
        num_targets = DAILY_SEND_LIMIT + 5
        targets = [_make_target_dict() for _ in range(num_targets)]

        with patch("src.services.email_service.send_outreach_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": "sent", "message_id": "m", "error": None}
            result = await send_bulk_outreach(
                session=AsyncMock(),
                campaign_id=uuid.uuid4(),
                subject="S",
                html_template="<p>Hi</p>",
                targets=targets,
            )

        assert result["sent"] == DAILY_SEND_LIMIT
        assert result["rate_limited"] == 5
        assert mock_send.call_count == DAILY_SEND_LIMIT

    @pytest.mark.asyncio
    async def test_rate_limited_entries_in_details(self):
        num_targets = DAILY_SEND_LIMIT + 2
        targets = [_make_target_dict() for _ in range(num_targets)]

        with patch("src.services.email_service.send_outreach_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": "sent", "message_id": "m", "error": None}
            result = await send_bulk_outreach(
                session=AsyncMock(),
                campaign_id=uuid.uuid4(),
                subject="S",
                html_template="<p>Hi</p>",
                targets=targets,
            )

        rate_limited_details = [d for d in result["details"] if d["status"] == "rate_limited"]
        assert len(rate_limited_details) == 2

    @pytest.mark.asyncio
    async def test_personalisation_substitution(self):
        """{{name}} in template should be replaced with personalization_data value."""
        target = {
            "target_id": uuid.uuid4(),
            "lead_id": uuid.uuid4(),
            "email": "t@example.com",
            "personalization_data": {"name": "Alice", "restaurant": "Joe's"},
        }
        captured_html: list = []

        async def mock_send(**kwargs):
            captured_html.append(kwargs["html_content"])
            return {"status": "sent", "message_id": "m", "error": None}

        with patch("src.services.email_service.send_outreach_email", side_effect=mock_send):
            await send_bulk_outreach(
                session=AsyncMock(),
                campaign_id=uuid.uuid4(),
                subject="S",
                html_template="<p>Hello {{name}}, from {{restaurant}}</p>",
                targets=[target],
            )

        assert captured_html[0] == "<p>Hello Alice, from Joe's</p>"

    @pytest.mark.asyncio
    async def test_empty_targets_returns_zero_counts(self):
        result = await send_bulk_outreach(
            session=AsyncMock(),
            campaign_id=uuid.uuid4(),
            subject="S",
            html_template="<p>Hi</p>",
            targets=[],
        )
        assert result["sent"] == 0
        assert result["failed"] == 0
        assert result["opted_out"] == 0
        assert result["rate_limited"] == 0
        assert result["details"] == []
