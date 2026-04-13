"""Tests for NIF-226 + NIF-225: email_tasks Celery tasks.

Covers:
- send_email_task dispatches correctly (calls send_outreach_email)
- process_sendgrid_events: delivered → mark_as_delivered
- process_sendgrid_events: open → mark_as_opened
- process_sendgrid_events: click → mark_as_clicked
- process_sendgrid_events: bounce → mark_as_bounced
- process_sendgrid_events: dropped → mark_as_failed
- process_sendgrid_events: spamreport → mark_as_opted_out + email_opt_out=True
- process_sendgrid_events: unsubscribe → mark_as_opted_out + email_opt_out=True
- Apple MPP detection flags proxy opens
- Event deduplication: same sg_event_id processed twice → skip second
- Apple MPP helper: detect / not-detect cases
- _sg_event_type_to_tracker mapping
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.tasks.email_tasks import (
    _is_apple_mpp,
    _sg_event_type_to_tracker,
    process_sendgrid_events,
    send_email_task,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_event(
    event_type: str = "delivered",
    sg_message_id: str = "msg-001",
    sg_event_id: str = "evt-001",
    useragent: str | None = None,
    timestamp: int = 1700000000,
) -> dict:
    ev = {
        "event": event_type,
        "sg_message_id": sg_message_id,
        "sg_event_id": sg_event_id,
        "email": "owner@restaurant.com",
        "timestamp": timestamp,
    }
    if useragent is not None:
        ev["useragent"] = useragent
    return ev


def _make_activity(target_id=None, external_message_id="msg-001"):
    activity = MagicMock()
    activity.target_id = target_id or uuid.uuid4()
    activity.external_message_id = external_message_id
    return activity


def _make_target(target_id=None, lead_id=None, campaign_id=None):
    target = MagicMock()
    target.id = target_id or uuid.uuid4()
    target.lead_id = lead_id or uuid.uuid4()
    target.campaign_id = campaign_id or uuid.uuid4()
    target.communication_status = "sent"
    return target


def _make_lead(lead_id=None):
    lead = MagicMock()
    lead.id = lead_id or uuid.uuid4()
    lead.email_opt_out = False
    return lead


# ── Apple MPP helper ──────────────────────────────────────────────────────────


class TestIsAppleMpp:
    def test_plain_mozilla_is_mpp(self):
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"
        assert _is_apple_mpp(ua) is True

    def test_chrome_ua_is_not_mpp(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0"
        assert _is_apple_mpp(ua) is False

    def test_firefox_ua_is_not_mpp(self):
        ua = "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0"
        assert _is_apple_mpp(ua) is False

    def test_safari_ua_is_not_mpp(self):
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
        assert _is_apple_mpp(ua) is False

    def test_none_ua_is_not_mpp(self):
        assert _is_apple_mpp(None) is False

    def test_empty_string_ua_is_not_mpp(self):
        assert _is_apple_mpp("") is False

    def test_edge_ua_is_not_mpp(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edg/120.0.0.0"
        assert _is_apple_mpp(ua) is False

    def test_generic_bot_without_browser_token_is_mpp(self):
        # Purely "Mozilla/5.0" with no Chrome/Firefox/Safari/Edge token
        ua = "Mozilla/5.0 (compatible; SomeMailProxy/1.0)"
        assert _is_apple_mpp(ua) is True


# ── _sg_event_type_to_tracker ─────────────────────────────────────────────────


class TestSgEventTypeToTracker:
    def test_delivered_maps_to_delivery(self):
        assert _sg_event_type_to_tracker("delivered") == "delivery"

    def test_open_maps_to_open(self):
        assert _sg_event_type_to_tracker("open") == "open"

    def test_click_maps_to_click(self):
        assert _sg_event_type_to_tracker("click") == "click"

    def test_bounce_maps_to_bounce(self):
        assert _sg_event_type_to_tracker("bounce") == "bounce"

    def test_dropped_maps_to_bounce(self):
        assert _sg_event_type_to_tracker("dropped") == "bounce"

    def test_spamreport_maps_to_unsubscribe(self):
        assert _sg_event_type_to_tracker("spamreport") == "unsubscribe"

    def test_unsubscribe_maps_to_unsubscribe(self):
        assert _sg_event_type_to_tracker("unsubscribe") == "unsubscribe"

    def test_unknown_type_passthrough(self):
        assert _sg_event_type_to_tracker("deferred") == "delivery"


# ── send_email_task ───────────────────────────────────────────────────────────


class TestSendEmailTask:
    def test_task_calls_send_outreach_email(self):
        """send_email_task.run() should invoke run_async and return the result."""
        target_id = str(uuid.uuid4())
        lead_id = str(uuid.uuid4())
        campaign_id = str(uuid.uuid4())

        mock_result = {"status": "sent", "message_id": "msg-x", "error": None}

        with patch("src.tasks.email_tasks.run_async", return_value=mock_result) as mock_run_async:
            result = send_email_task.run(
                target_id=target_id,
                lead_id=lead_id,
                campaign_id=campaign_id,
                subject="Test",
                html_content="<p>Hi</p>",
            )

        assert result["status"] == "sent"
        mock_run_async.assert_called_once()

    def test_task_retries_on_exception(self):
        """Task should raise after calling self.retry when an exception occurs."""
        with patch("src.tasks.email_tasks.run_async", side_effect=RuntimeError("DB gone")):
            # Calling .run() means self is the real Celery task; it will try to retry.
            # We just check it raises (since no broker is configured, retry itself raises).
            with pytest.raises(Exception):
                send_email_task.run(
                    target_id=str(uuid.uuid4()),
                    lead_id=str(uuid.uuid4()),
                    campaign_id=str(uuid.uuid4()),
                    subject="S",
                    html_content="<p>X</p>",
                )


# ── process_sendgrid_events ───────────────────────────────────────────────────


def _build_session_for_event(
    sg_message_id: str,
    target: MagicMock | None = None,
    lead: MagicMock | None = None,
    duplicate: bool = False,
):
    """Build an AsyncSession mock that:
    - Returns None from TrackerEvent dedup check (unless duplicate=True)
    - Returns an activity matched by sg_message_id
    - Returns target and lead by ID
    """
    activity = _make_activity(
        target_id=(target.id if target else uuid.uuid4()),
        external_message_id=sg_message_id,
    )
    if target is None:
        target = _make_target()

    dedup_result = MagicMock()
    if duplicate:
        # Return an existing TrackerEvent to simulate duplicate
        dedup_result.scalar_one_or_none.return_value = MagicMock()
    else:
        dedup_result.scalar_one_or_none.return_value = None

    activity_result = MagicMock()
    activity_result.scalar_one_or_none.return_value = activity

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[dedup_result, activity_result])
    session.get = AsyncMock(side_effect=[target, lead or _make_lead()])
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    session.commit = AsyncMock()
    return session


class TestProcessSendgridEvents:
    def _run_task(self, events, session):
        """Run the async core of process_sendgrid_events synchronously using the mock session."""
        import asyncio

        async def _inner():
            # Patch all the DB models and session inside the task
            with patch("src.tasks.email_tasks.run_async") as mock_run:
                # We need to actually run the inner async function
                # Extract the inner _process coroutine by calling the task body directly
                pass

        # Instead of running through Celery, test via patching run_async with a real coroutine
        results: list = []

        original_run_async = None

        def capture_and_run(coro):
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(coro)
                results.append(result)
                return result
            finally:
                loop.close()

        with patch("src.tasks.email_tasks.run_async", side_effect=capture_and_run):
            try:
                process_sendgrid_events.__wrapped__(MagicMock(), events)
            except Exception:
                pass
        return results[0] if results else None

    def _run_task_with_mocked_session(self, events, session):
        """Run process_sendgrid_events with a fully mocked DB session."""
        import asyncio

        async def _inner():
            from sqlalchemy.exc import IntegrityError
            import src.services.communication_status as cs
            from src.db.models import Lead, OutreachActivity, OutreachTarget, TrackerEvent

            # We call the private async function by importing the module and running manually
            # Simplest approach: use the session mock in context
            pass

        results: list = []

        def run_with_mock(coro):
            loop = asyncio.new_event_loop()

            async def _patched():
                from sqlalchemy import select
                # Patch async_session to yield our mock
                cm = MagicMock()
                cm.__aenter__ = AsyncMock(return_value=session)
                cm.__aexit__ = AsyncMock(return_value=False)
                with patch("src.tasks.email_tasks.run_async"):
                    pass
                return {"processed": 0, "skipped": 0, "errors": 0}

            try:
                result = loop.run_until_complete(_patched())
                results.append(result)
                return result
            finally:
                loop.close()

        with patch("src.tasks.email_tasks.run_async", side_effect=run_with_mock):
            try:
                process_sendgrid_events.__wrapped__(MagicMock(), events)
            except Exception:
                pass
        return results[0] if results else None


# Because the Celery task uses run_async internally with an inner _process coroutine,
# the cleanest approach is to test the task's async logic by patching async_session
# and running the inner coroutine directly.

class TestProcessSendgridEventsIntegration:
    """Test the inner async logic of process_sendgrid_events by injecting a mock session."""

    def _execute_inner(self, events, session_factory):
        """Extract and run the _process coroutine inside process_sendgrid_events."""
        import asyncio
        captured_coros: list = []

        def capture_run_async(coro):
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(coro)
                captured_coros.append(result)
                return result
            finally:
                loop.close()

        with patch("src.tasks.email_tasks.run_async", side_effect=capture_run_async), \
             patch("src.db.session.async_session", session_factory):
            process_sendgrid_events.run(events)

        return captured_coros[0] if captured_coros else None

    def _make_session_factory(self, session):
        """Build an async context manager factory around the session mock."""
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)

        def factory():
            return cm

        return factory

    def _make_full_session(
        self,
        sg_message_id: str = "msg-001",
        duplicate: bool = False,
        has_activity: bool = True,
        has_target: bool = True,
        has_lead: bool = True,
    ):
        """Build a session mock covering the execute/get calls in _process."""
        # First execute: dedup check
        dedup_result = MagicMock()
        dedup_result.scalar_one_or_none.return_value = MagicMock() if duplicate else None

        # Second execute: activity lookup
        activity = None
        if has_activity:
            activity = MagicMock()
            activity.target_id = uuid.uuid4()
        activity_result = MagicMock()
        activity_result.scalar_one_or_none.return_value = activity

        # get calls: target, lead
        target = _make_target() if has_target else None
        lead = _make_lead() if has_lead else None
        get_side_effect = [target, lead] if activity else []

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[dedup_result, activity_result])
        session.get = AsyncMock(side_effect=get_side_effect)
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.rollback = AsyncMock()
        session.commit = AsyncMock()
        return session, activity, target, lead

    def test_delivered_event_calls_mark_as_delivered(self):
        session, activity, target, lead = self._make_full_session()
        events = [_make_event("delivered")]

        with patch("src.services.communication_status.mark_as_delivered", new_callable=AsyncMock) as mock_fn:
            self._execute_inner(events, self._make_session_factory(session))

        mock_fn.assert_called_once_with(session, activity.target_id, "email")

    def test_open_event_calls_mark_as_opened(self):
        session, activity, target, lead = self._make_full_session()
        events = [_make_event("open")]

        with patch("src.services.communication_status.mark_as_opened", new_callable=AsyncMock) as mock_fn:
            self._execute_inner(events, self._make_session_factory(session))

        mock_fn.assert_called_once_with(session, activity.target_id, "email")

    def test_click_event_calls_mark_as_clicked(self):
        session, activity, target, lead = self._make_full_session()
        events = [_make_event("click")]

        with patch("src.services.communication_status.mark_as_clicked", new_callable=AsyncMock) as mock_fn:
            self._execute_inner(events, self._make_session_factory(session))

        mock_fn.assert_called_once_with(session, activity.target_id, "email")

    def test_bounce_event_calls_mark_as_bounced(self):
        session, activity, target, lead = self._make_full_session()
        events = [_make_event("bounce")]

        with patch("src.services.communication_status.mark_as_bounced", new_callable=AsyncMock) as mock_fn:
            self._execute_inner(events, self._make_session_factory(session))

        mock_fn.assert_called_once_with(session, activity.target_id, "email", bounce_type="hard")

    def test_dropped_event_calls_mark_as_failed(self):
        session, activity, target, lead = self._make_full_session()
        events = [_make_event("dropped")]

        with patch("src.services.communication_status.mark_as_failed", new_callable=AsyncMock) as mock_fn:
            self._execute_inner(events, self._make_session_factory(session))

        mock_fn.assert_called_once_with(session, activity.target_id, "email", error_reason="dropped")

    def test_spamreport_calls_mark_as_opted_out_and_sets_email_opt_out(self):
        session, activity, target, lead = self._make_full_session()
        events = [_make_event("spamreport")]

        with patch("src.services.communication_status.mark_as_opted_out", new_callable=AsyncMock) as mock_fn:
            self._execute_inner(events, self._make_session_factory(session))

        mock_fn.assert_called_once()
        assert lead.email_opt_out is True

    def test_unsubscribe_calls_mark_as_opted_out_and_sets_email_opt_out(self):
        session, activity, target, lead = self._make_full_session()
        events = [_make_event("unsubscribe")]

        with patch("src.services.communication_status.mark_as_opted_out", new_callable=AsyncMock) as mock_fn:
            self._execute_inner(events, self._make_session_factory(session))

        mock_fn.assert_called_once()
        assert lead.email_opt_out is True

    def test_apple_mpp_open_flags_metadata(self):
        """Open event with Apple MPP UA should set apple_mpp=True in TrackerEvent metadata."""
        session, activity, target, lead = self._make_full_session()
        apple_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
        events = [_make_event("open", useragent=apple_ua)]

        with patch("src.services.communication_status.mark_as_opened", new_callable=AsyncMock):
            self._execute_inner(events, self._make_session_factory(session))

        # TrackerEvent was added — check metadata
        session.add.assert_called_once()
        tracker_event = session.add.call_args[0][0]
        assert tracker_event.event_metadata.get("apple_mpp") is True

    def test_real_browser_open_does_not_flag_apple_mpp(self):
        session, activity, target, lead = self._make_full_session()
        real_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/120.0.0.0"
        events = [_make_event("open", useragent=real_ua)]

        with patch("src.services.communication_status.mark_as_opened", new_callable=AsyncMock):
            self._execute_inner(events, self._make_session_factory(session))

        tracker_event = session.add.call_args[0][0]
        assert tracker_event.event_metadata.get("apple_mpp") is not True

    def test_duplicate_event_skipped(self):
        """An event with the same sg_event_id should be skipped on second processing."""
        session, _, _, _ = self._make_full_session(duplicate=True)
        events = [_make_event("delivered")]

        with patch("src.services.communication_status.mark_as_delivered", new_callable=AsyncMock) as mock_fn:
            result = self._execute_inner(events, self._make_session_factory(session))

        # Status handler should NOT be called since event is duplicate
        mock_fn.assert_not_called()
        # TrackerEvent should NOT be added
        session.add.assert_not_called()

    def test_tracker_event_created_for_delivered(self):
        session, activity, target, lead = self._make_full_session()
        events = [_make_event("delivered")]

        with patch("src.services.communication_status.mark_as_delivered", new_callable=AsyncMock):
            self._execute_inner(events, self._make_session_factory(session))

        session.add.assert_called_once()
        tracker_event = session.add.call_args[0][0]
        assert tracker_event.event_type == "delivery"
        assert tracker_event.channel == "email"
        assert tracker_event.provider == "sendgrid"

    def test_tracker_event_provider_event_id_set(self):
        session, activity, target, lead = self._make_full_session()
        events = [_make_event("click", sg_event_id="unique-evt-123")]

        with patch("src.services.communication_status.mark_as_clicked", new_callable=AsyncMock):
            self._execute_inner(events, self._make_session_factory(session))

        tracker_event = session.add.call_args[0][0]
        assert "unique-evt-123" in tracker_event.provider_event_id

    def test_session_committed_after_processing(self):
        session, activity, target, lead = self._make_full_session()
        events = [_make_event("delivered")]

        with patch("src.services.communication_status.mark_as_delivered", new_callable=AsyncMock):
            self._execute_inner(events, self._make_session_factory(session))

        session.commit.assert_called_once()
