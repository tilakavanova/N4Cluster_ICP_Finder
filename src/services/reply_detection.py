"""Email reply detection service (NIF-229).

Parses SendGrid Inbound Parse webhook payloads to detect email replies,
match them to the originating OutreachActivity, and trigger the REPLIED
status transition.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import src.services.communication_status as cs
from src.utils.logging import get_logger

logger = get_logger("reply_detection")

# Regex to extract a Message-ID value from a raw headers string
_MESSAGE_ID_RE = re.compile(r"Message-ID:\s*<([^>]+)>", re.IGNORECASE)
_IN_REPLY_TO_RE = re.compile(r"In-Reply-To:\s*<([^>]+)>", re.IGNORECASE)
_REFERENCES_RE = re.compile(r"References:.*?<([^>]+)>", re.IGNORECASE | re.DOTALL)
_X_ACTIVITY_RE = re.compile(r"X-Outreach-Activity-Id:\s*([^\r\n]+)", re.IGNORECASE)
_X_LEAD_RE = re.compile(r"X-Lead-Id:\s*([^\r\n]+)", re.IGNORECASE)


def _extract_header(headers_str: str, pattern: re.Pattern) -> str | None:
    """Extract the first capture group from a raw headers string."""
    m = pattern.search(headers_str)
    return m.group(1).strip() if m else None


def detect_reply(inbound_email_data: dict) -> dict:
    """Parse a SendGrid Inbound Parse webhook payload and extract reply context.

    SendGrid Inbound Parse POSTs multipart/form-data with these key fields:
        - ``headers``: raw RFC 2822 headers as a string
        - ``from``: sender address
        - ``to``: recipient address
        - ``subject``: email subject
        - ``text``: plain-text body
        - ``html``: HTML body
        - ``envelope``: JSON string with ``{from, to}``

    Returns a dict with:
        ``{
            "in_reply_to": str | None,       # Message-ID being replied to
            "references": str | None,         # First reference ID
            "activity_id": str | None,        # X-Outreach-Activity-Id header
            "lead_id": str | None,            # X-Lead-Id header
            "from_email": str | None,         # Sender address
            "to_email": str | None,           # Recipient address
            "subject": str | None,
            "text_body": str | None,
            "is_likely_reply": bool,          # True if In-Reply-To or References found
        }``
    """
    raw_headers: str = inbound_email_data.get("headers", "")
    from_email: str | None = inbound_email_data.get("from")
    to_email: str | None = inbound_email_data.get("to")
    subject: str | None = inbound_email_data.get("subject")
    text_body: str | None = inbound_email_data.get("text")

    in_reply_to = _extract_header(raw_headers, _IN_REPLY_TO_RE)
    references = _extract_header(raw_headers, _REFERENCES_RE)
    activity_id = _extract_header(raw_headers, _X_ACTIVITY_RE)
    lead_id = _extract_header(raw_headers, _X_LEAD_RE)

    # Normalise from_email: "Name <addr>" → "addr"
    if from_email:
        m = re.search(r"<([^>]+)>", from_email)
        if m:
            from_email = m.group(1).strip()

    is_likely_reply = bool(in_reply_to or references or (subject and subject.lower().startswith("re:")))

    return {
        "in_reply_to": in_reply_to,
        "references": references,
        "activity_id": activity_id,
        "lead_id": lead_id,
        "from_email": from_email,
        "to_email": to_email,
        "subject": subject,
        "text_body": text_body,
        "is_likely_reply": is_likely_reply,
    }


async def process_inbound_reply(session, reply_data: dict) -> dict:
    """Match a parsed inbound reply to an OutreachActivity and record it.

    Matching order:
    1. X-Outreach-Activity-Id custom header (most precise)
    2. In-Reply-To → external_message_id on OutreachActivity
    3. References → external_message_id fallback

    On match:
    - Calls ``mark_as_replied`` on the OutreachTarget
    - Creates a TrackerEvent(event_type="reply")

    Returns a dict with ``{matched: bool, activity_id, target_id, lead_id}``.
    """
    import uuid as _uuid

    from sqlalchemy import select

    from src.db.models import Lead, OutreachActivity, OutreachTarget, TrackerEvent

    activity: OutreachActivity | None = None

    # 1. Try X-Outreach-Activity-Id
    if reply_data.get("activity_id"):
        try:
            activity = await session.get(OutreachActivity, _uuid.UUID(reply_data["activity_id"]))
        except (ValueError, AttributeError):
            pass

    # 2. Try In-Reply-To → external_message_id
    if activity is None and reply_data.get("in_reply_to"):
        result = await session.execute(
            select(OutreachActivity).where(
                OutreachActivity.external_message_id == reply_data["in_reply_to"]
            )
        )
        activity = result.scalar_one_or_none()

    # 3. Fallback: References header
    if activity is None and reply_data.get("references"):
        result = await session.execute(
            select(OutreachActivity).where(
                OutreachActivity.external_message_id == reply_data["references"]
            )
        )
        activity = result.scalar_one_or_none()

    if activity is None:
        logger.info(
            "inbound_reply_unmatched",
            from_email=reply_data.get("from_email"),
            in_reply_to=reply_data.get("in_reply_to"),
        )
        return {"matched": False, "activity_id": None, "target_id": None, "lead_id": None}

    target = await session.get(OutreachTarget, activity.target_id)
    lead_id: _uuid.UUID | None = target.lead_id if target else None
    campaign_id: _uuid.UUID | None = target.campaign_id if target else None

    # Transition status to REPLIED
    try:
        await cs.mark_as_replied(session, activity.target_id, "email")
    except Exception as exc:
        logger.warning("reply_status_transition_failed", error=str(exc), activity_id=str(activity.id))

    # Record TrackerEvent
    tracker = TrackerEvent(
        token=None,
        event_type="reply",
        channel="email",
        lead_id=lead_id,
        campaign_id=campaign_id,
        target_id=activity.target_id,
        provider="sendgrid_inbound",
        provider_event_id=f"reply:{activity.id}:{reply_data.get('in_reply_to', '')}",
        event_metadata={
            "from_email": reply_data.get("from_email"),
            "subject": reply_data.get("subject"),
            "in_reply_to": reply_data.get("in_reply_to"),
        },
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(tracker)
    await session.flush()

    logger.info(
        "inbound_reply_matched",
        activity_id=str(activity.id),
        target_id=str(activity.target_id),
        from_email=reply_data.get("from_email"),
    )

    return {
        "matched": True,
        "activity_id": str(activity.id),
        "target_id": str(activity.target_id),
        "lead_id": str(lead_id) if lead_id else None,
    }
