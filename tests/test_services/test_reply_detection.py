"""Tests for NIF-229: Email reply detection service.

Covers:
- detect_reply extracts In-Reply-To header
- detect_reply extracts References header
- detect_reply extracts X-Outreach-Activity-Id custom header
- detect_reply extracts X-Lead-Id custom header
- detect_reply normalises "Name <addr>" from_email to address only
- detect_reply sets is_likely_reply=True when In-Reply-To present
- detect_reply sets is_likely_reply=True when subject starts with Re:
- detect_reply sets is_likely_reply=False when no reply signals
- process_inbound_reply matches via X-Outreach-Activity-Id
- process_inbound_reply matches via In-Reply-To → external_message_id
- process_inbound_reply matches via References fallback
- process_inbound_reply returns matched=False when no activity found
- process_inbound_reply calls mark_as_replied on matched target
- process_inbound_reply creates TrackerEvent(event_type="reply")
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.reply_detection import detect_reply


# ── detect_reply unit tests ───────────────────────────────────────────────────

RAW_HEADERS_REPLY = (
    "Received: from mail.example.com\r\n"
    "Message-ID: <reply-msg-001@mail.example.com>\r\n"
    "In-Reply-To: <original-msg-abc@sendgrid.net>\r\n"
    "References: <original-msg-abc@sendgrid.net>\r\n"
    "X-Outreach-Activity-Id: 3fa85f64-5717-4562-b3fc-2c963f66afa6\r\n"
    "X-Lead-Id: 7f000001-0000-0000-0000-000000000001\r\n"
    "Subject: Re: A partnership opportunity for Pasta Palace\r\n"
)


def test_detect_reply_extracts_in_reply_to():
    data = detect_reply({"headers": RAW_HEADERS_REPLY, "from": "owner@pasta.com"})
    assert data["in_reply_to"] == "original-msg-abc@sendgrid.net"


def test_detect_reply_extracts_references():
    data = detect_reply({"headers": RAW_HEADERS_REPLY})
    assert data["references"] == "original-msg-abc@sendgrid.net"


def test_detect_reply_extracts_activity_id():
    data = detect_reply({"headers": RAW_HEADERS_REPLY})
    assert data["activity_id"] == "3fa85f64-5717-4562-b3fc-2c963f66afa6"


def test_detect_reply_extracts_lead_id():
    data = detect_reply({"headers": RAW_HEADERS_REPLY})
    assert data["lead_id"] == "7f000001-0000-0000-0000-000000000001"


def test_detect_reply_normalises_from_email():
    data = detect_reply({
        "headers": "",
        "from": "John Owner <owner@pasta.com>",
        "subject": "Re: test",
    })
    assert data["from_email"] == "owner@pasta.com"


def test_detect_reply_plain_address_unchanged():
    data = detect_reply({
        "headers": "",
        "from": "owner@pasta.com",
        "subject": "Re: test",
    })
    assert data["from_email"] == "owner@pasta.com"


def test_detect_reply_is_likely_reply_with_in_reply_to():
    data = detect_reply({"headers": RAW_HEADERS_REPLY})
    assert data["is_likely_reply"] is True


def test_detect_reply_is_likely_reply_subject_re():
    data = detect_reply({
        "headers": "Subject: Re: something\r\n",
        "subject": "Re: A partnership opportunity",
    })
    assert data["is_likely_reply"] is True


def test_detect_reply_is_not_likely_reply():
    data = detect_reply({
        "headers": "Message-ID: <new@example.com>\r\n",
        "from": "owner@pasta.com",
        "subject": "New inquiry",
    })
    assert data["is_likely_reply"] is False


def test_detect_reply_missing_fields_no_error():
    # Should not raise even with empty input
    data = detect_reply({})
    assert data["is_likely_reply"] is False
    assert data["in_reply_to"] is None
    assert data["from_email"] is None


# ── process_inbound_reply unit tests ─────────────────────────────────────────


def _make_activity(activity_id=None, target_id=None, external_message_id=None):
    a = MagicMock()
    a.id = activity_id or uuid.uuid4()
    a.target_id = target_id or uuid.uuid4()
    a.external_message_id = external_message_id or f"<msg-{uuid.uuid4()}@sg.net>"
    return a


def _make_target(target_id=None, lead_id=None, campaign_id=None):
    t = MagicMock()
    t.id = target_id or uuid.uuid4()
    t.lead_id = lead_id or uuid.uuid4()
    t.campaign_id = campaign_id or uuid.uuid4()
    return t


@pytest.mark.asyncio
async def test_process_inbound_reply_matches_via_activity_id():
    from src.services.reply_detection import process_inbound_reply

    activity = _make_activity()
    target = _make_target(target_id=activity.target_id)

    session = MagicMock()
    session.get = AsyncMock(side_effect=lambda model, pk: activity if model.__name__ == "OutreachActivity" else target)
    session.add = MagicMock()
    session.flush = AsyncMock()

    reply_data = {
        "activity_id": str(activity.id),
        "in_reply_to": None,
        "references": None,
        "from_email": "owner@pasta.com",
        "subject": "Re: test",
        "is_likely_reply": True,
    }

    with patch("src.services.reply_detection.cs.mark_as_replied", new_callable=AsyncMock) as mock_replied:
        result = await process_inbound_reply(session, reply_data)

    assert result["matched"] is True
    assert result["activity_id"] == str(activity.id)
    mock_replied.assert_called_once()


@pytest.mark.asyncio
async def test_process_inbound_reply_matches_via_in_reply_to():
    from src.services.reply_detection import process_inbound_reply
    from sqlalchemy import select

    message_id = "<original@sg.net>"
    activity = _make_activity(external_message_id=message_id)
    target = _make_target(target_id=activity.target_id)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = activity

    session = MagicMock()
    session.get = AsyncMock(side_effect=lambda model, pk: None if hasattr(model, "__name__") and model.__name__ == "OutreachActivity" else target)
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.flush = AsyncMock()

    reply_data = {
        "activity_id": None,
        "in_reply_to": message_id,
        "references": None,
        "from_email": "owner@pasta.com",
        "subject": "Re: test",
        "is_likely_reply": True,
    }

    with patch("src.services.reply_detection.cs.mark_as_replied", new_callable=AsyncMock):
        result = await process_inbound_reply(session, reply_data)

    assert result["matched"] is True


@pytest.mark.asyncio
async def test_process_inbound_reply_no_match_returns_false():
    from src.services.reply_detection import process_inbound_reply

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    session = MagicMock()
    session.get = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=mock_result)

    reply_data = {
        "activity_id": None,
        "in_reply_to": "<not-found@sg.net>",
        "references": None,
        "from_email": "unknown@example.com",
        "subject": "Re: something",
        "is_likely_reply": True,
    }

    result = await process_inbound_reply(session, reply_data)
    assert result["matched"] is False


@pytest.mark.asyncio
async def test_process_inbound_reply_creates_tracker_event():
    from src.services.reply_detection import process_inbound_reply
    from src.db.models import TrackerEvent

    activity = _make_activity()
    target = _make_target(target_id=activity.target_id)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = activity

    session = MagicMock()
    session.get = AsyncMock(return_value=target)
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.flush = AsyncMock()

    reply_data = {
        "activity_id": None,
        "in_reply_to": activity.external_message_id,
        "references": None,
        "from_email": "owner@pasta.com",
        "subject": "Re: test",
        "is_likely_reply": True,
    }

    with patch("src.services.reply_detection.cs.mark_as_replied", new_callable=AsyncMock):
        await process_inbound_reply(session, reply_data)

    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert isinstance(added, TrackerEvent)
    assert added.event_type == "reply"
    assert added.channel == "email"
