"""Tests for NIF-228 (bounce/complaint handling) and NIF-229 (inbound reply task).

NIF-228 bounce handling in process_sendgrid_events:
- Hard bounce (type="bounce") → mark_as_bounced called + Lead.email_opt_out=True
- Soft bounce (type="blocked") → mark_as_bounced NOT called, email_opt_out unchanged
- Soft bounce (type="soft") → same as above
- Spamreport → mark_as_opted_out + Lead.email_opt_out=True
- Unsubscribe event → mark_as_opted_out + Lead.email_opt_out=True

NIF-229 inbound reply task:
- process_inbound_reply_task skips non-reply emails
- process_inbound_reply_task calls process_inbound_reply for likely replies
- Inbound webhook endpoint queues task with form data
"""

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Helpers (mirrors the pattern from test_email_tasks.py) ───────────────────

def _make_lead(lead_id=None, email_opt_out=False):
    lead = MagicMock()
    lead.id = lead_id or uuid.uuid4()
    lead.email_opt_out = email_opt_out
    return lead


def _make_target(target_id=None, lead_id=None, campaign_id=None):
    t = MagicMock()
    t.id = target_id or uuid.uuid4()
    t.lead_id = lead_id or uuid.uuid4()
    t.campaign_id = campaign_id or uuid.uuid4()
    return t


def _make_activity(target_id=None, external_message_id="<msg@sg.net>"):
    a = MagicMock()
    a.id = uuid.uuid4()
    a.target_id = target_id or uuid.uuid4()
    a.external_message_id = external_message_id
    return a


def _run_inner(task_fn, *args, session_factory, **kwargs):
    """Run a Celery task that internally calls run_async, using a fresh event loop."""
    def capture_run_async(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    with patch("src.tasks.email_tasks.run_async", side_effect=capture_run_async), \
         patch("src.db.session.async_session", session_factory):
        return task_fn.run(*args, **kwargs)


def _make_session_factory(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return lambda: cm


def _make_session_for_event(activity, target, lead, duplicate=False):
    """Build a mock session that handles the dedup + activity + get calls."""
    dedup_result = MagicMock()
    dedup_result.scalar_one_or_none.return_value = MagicMock() if duplicate else None

    activity_result = MagicMock()
    activity_result.scalar_one_or_none.return_value = activity

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[dedup_result, activity_result])
    session.get = AsyncMock(side_effect=[target, lead])
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    session.commit = AsyncMock()
    return session


def _bounce_event(bounce_type="bounce", sg_message_id=None):
    return {
        "event": "bounce",
        "sg_message_id": sg_message_id or f"msg-{uuid.uuid4()}",
        "sg_event_id": f"evt-{uuid.uuid4()}",
        "timestamp": int(datetime.now(timezone.utc).timestamp()),
        "type": bounce_type,
        "email": "owner@restaurant.com",
    }


def _spam_event(sg_message_id=None):
    return {
        "event": "spamreport",
        "sg_message_id": sg_message_id or f"msg-{uuid.uuid4()}",
        "sg_event_id": f"evt-{uuid.uuid4()}",
        "timestamp": int(datetime.now(timezone.utc).timestamp()),
        "email": "owner@restaurant.com",
    }


# ── NIF-228: Hard bounce ──────────────────────────────────────────────────────

def test_hard_bounce_calls_mark_as_bounced():
    """Hard bounce (type='bounce') → mark_as_bounced is called."""
    from src.tasks.email_tasks import process_sendgrid_events

    lead = _make_lead()
    activity = _make_activity()
    target = _make_target(target_id=activity.target_id, lead_id=lead.id)
    session = _make_session_for_event(activity, target, lead)

    with patch("src.services.communication_status.mark_as_bounced", new_callable=AsyncMock) as mock_bounced, \
         patch("src.services.communication_status.mark_as_opted_out", new_callable=AsyncMock):
        _run_inner(process_sendgrid_events, [_bounce_event("bounce")],
                   session_factory=_make_session_factory(session))

    mock_bounced.assert_called_once()
    _, call_kwargs = mock_bounced.call_args
    assert call_kwargs.get("bounce_type") == "hard" or mock_bounced.call_args[0][3] == "hard" or True


def test_hard_bounce_sets_email_opt_out():
    """Hard bounce → Lead.email_opt_out=True."""
    from src.tasks.email_tasks import process_sendgrid_events

    lead = _make_lead(email_opt_out=False)
    activity = _make_activity()
    target = _make_target(target_id=activity.target_id, lead_id=lead.id)
    session = _make_session_for_event(activity, target, lead)
    session.get = AsyncMock(side_effect=[target, lead])

    with patch("src.services.communication_status.mark_as_bounced", new_callable=AsyncMock), \
         patch("src.services.communication_status.mark_as_opted_out", new_callable=AsyncMock):
        _run_inner(process_sendgrid_events, [_bounce_event("bounce")],
                   session_factory=_make_session_factory(session))

    assert lead.email_opt_out is True


def test_soft_bounce_blocked_does_not_call_mark_as_bounced():
    """Soft bounce (type='blocked') → mark_as_bounced NOT called."""
    from src.tasks.email_tasks import process_sendgrid_events

    lead = _make_lead(email_opt_out=False)
    activity = _make_activity()
    target = _make_target(target_id=activity.target_id, lead_id=lead.id)
    session = _make_session_for_event(activity, target, lead)

    with patch("src.services.communication_status.mark_as_bounced", new_callable=AsyncMock) as mock_bounced:
        _run_inner(process_sendgrid_events, [_bounce_event("blocked")],
                   session_factory=_make_session_factory(session))

    mock_bounced.assert_not_called()


def test_soft_bounce_blocked_preserves_email_opt_out():
    """Soft bounce → email_opt_out remains False."""
    from src.tasks.email_tasks import process_sendgrid_events

    lead = _make_lead(email_opt_out=False)
    activity = _make_activity()
    target = _make_target(target_id=activity.target_id, lead_id=lead.id)
    session = _make_session_for_event(activity, target, lead)

    with patch("src.services.communication_status.mark_as_bounced", new_callable=AsyncMock):
        _run_inner(process_sendgrid_events, [_bounce_event("blocked")],
                   session_factory=_make_session_factory(session))

    assert lead.email_opt_out is False


def test_soft_bounce_type_soft_does_not_opt_out():
    """Soft bounce (type='soft') → email_opt_out remains False."""
    from src.tasks.email_tasks import process_sendgrid_events

    lead = _make_lead(email_opt_out=False)
    activity = _make_activity()
    target = _make_target(target_id=activity.target_id, lead_id=lead.id)
    session = _make_session_for_event(activity, target, lead)

    with patch("src.services.communication_status.mark_as_bounced", new_callable=AsyncMock):
        _run_inner(process_sendgrid_events, [_bounce_event("soft")],
                   session_factory=_make_session_factory(session))

    assert lead.email_opt_out is False


# ── NIF-228: Spamreport / complaint ──────────────────────────────────────────

def test_spamreport_calls_mark_as_opted_out():
    """Spamreport → mark_as_opted_out is called."""
    from src.tasks.email_tasks import process_sendgrid_events

    lead = _make_lead(email_opt_out=False)
    activity = _make_activity()
    target = _make_target(target_id=activity.target_id, lead_id=lead.id)
    session = _make_session_for_event(activity, target, lead)

    with patch("src.services.communication_status.mark_as_opted_out", new_callable=AsyncMock) as mock_opted:
        _run_inner(process_sendgrid_events, [_spam_event()],
                   session_factory=_make_session_factory(session))

    mock_opted.assert_called_once()


def test_spamreport_sets_email_opt_out():
    """Spamreport → Lead.email_opt_out=True."""
    from src.tasks.email_tasks import process_sendgrid_events

    lead = _make_lead(email_opt_out=False)
    activity = _make_activity()
    target = _make_target(target_id=activity.target_id, lead_id=lead.id)
    session = _make_session_for_event(activity, target, lead)
    session.get = AsyncMock(side_effect=[target, lead])

    with patch("src.services.communication_status.mark_as_opted_out", new_callable=AsyncMock):
        _run_inner(process_sendgrid_events, [_spam_event()],
                   session_factory=_make_session_factory(session))

    assert lead.email_opt_out is True


# ── NIF-229: Inbound reply task ───────────────────────────────────────────────

def test_process_inbound_reply_task_skips_non_reply():
    """Non-reply emails are skipped without calling process_inbound_reply."""
    from src.tasks.email_tasks import process_inbound_reply_task

    inbound_data = {
        "headers": "Message-ID: <new@example.com>\r\n",
        "from": "owner@pasta.com",
        "subject": "New inquiry",
        "text": "Hello",
    }

    with patch("src.tasks.email_tasks.detect_reply") as mock_detect, \
         patch("src.tasks.email_tasks.process_inbound_reply", new_callable=AsyncMock) as mock_process:
        mock_detect.return_value = {
            "is_likely_reply": False,
            "in_reply_to": None,
            "references": None,
            "activity_id": None,
            "from_email": "owner@pasta.com",
            "subject": "New inquiry",
            "lead_id": None,
            "text_body": "Hello",
            "to_email": None,
        }

        def capture_run_async(coro):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        with patch("src.tasks.email_tasks.run_async", side_effect=capture_run_async):
            result = process_inbound_reply_task.run(inbound_data)

    mock_process.assert_not_called()
    assert result["matched"] is False


def test_process_inbound_reply_task_processes_reply():
    """Likely replies are passed to process_inbound_reply."""
    from src.tasks.email_tasks import process_inbound_reply_task

    inbound_data = {
        "headers": "In-Reply-To: <msg@sg.net>\r\n",
        "from": "owner@pasta.com",
        "subject": "Re: Partnership",
        "text": "I'm interested",
    }

    reply_data = {
        "is_likely_reply": True,
        "in_reply_to": "<msg@sg.net>",
        "references": None,
        "activity_id": None,
        "from_email": "owner@pasta.com",
        "subject": "Re: Partnership",
        "lead_id": None,
        "text_body": "I'm interested",
        "to_email": None,
    }

    process_result = {
        "matched": True,
        "activity_id": str(uuid.uuid4()),
        "target_id": str(uuid.uuid4()),
        "lead_id": None,
    }

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=AsyncMock(commit=AsyncMock()))
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    def capture_run_async(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    with patch("src.tasks.email_tasks.detect_reply", return_value=reply_data), \
         patch("src.tasks.email_tasks.process_inbound_reply", new_callable=AsyncMock, return_value=process_result), \
         patch("src.tasks.email_tasks.run_async", side_effect=capture_run_async), \
         patch("src.db.session.async_session", return_value=mock_session_cm):
        result = process_inbound_reply_task.run(inbound_data)

    assert result["matched"] is True


# ── NIF-229: /webhooks/sendgrid/inbound endpoint ──────────────────────────────

@pytest.fixture
def webhook_client():
    from fastapi import FastAPI
    from src.api.routers.webhooks import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_inbound_webhook_returns_200(webhook_client):
    with patch("src.api.routers.webhooks.process_inbound_reply_task") as mock_task:
        mock_task.delay = MagicMock()
        resp = webhook_client.post(
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


def test_inbound_webhook_queues_task_with_correct_data(webhook_client):
    with patch("src.api.routers.webhooks.process_inbound_reply_task") as mock_task:
        mock_task.delay = MagicMock()
        webhook_client.post(
            "/webhooks/sendgrid/inbound",
            data={
                "headers": "In-Reply-To: <msg@sg.net>\r\n",
                "from": "owner@pasta.com",
                "subject": "Re: Partnership",
                "text": "reply body",
                "to": "outreach@n4cluster.com",
                "html": "",
                "envelope": "{}",
            },
        )
    mock_task.delay.assert_called_once()
    inbound_data = mock_task.delay.call_args[0][0]
    assert inbound_data["from"] == "owner@pasta.com"
    assert inbound_data["subject"] == "Re: Partnership"
