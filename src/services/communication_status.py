"""Unified communication status state machine (NIF-222).

Tracks the full lifecycle of a communication across all channels:
QUEUED → SENT → DELIVERED → OPENED → CLICKED → REPLIED
                    ↘ BOUNCED
                    ↘ FAILED
         ↘ FAILED
QUEUED → OPTED_OUT (if opt-out detected before send)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OutreachTarget, TrackerEvent
from src.utils.logging import get_logger

logger = get_logger("communication_status")


class CommunicationStatus(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    OPENED = "opened"
    CLICKED = "clicked"
    REPLIED = "replied"
    BOUNCED = "bounced"
    FAILED = "failed"
    OPTED_OUT = "opted_out"


# Valid transitions: current_status → set of allowed next statuses
_VALID_TRANSITIONS: dict[CommunicationStatus, set[CommunicationStatus]] = {
    CommunicationStatus.QUEUED: {
        CommunicationStatus.SENT,
        CommunicationStatus.FAILED,
        CommunicationStatus.OPTED_OUT,
    },
    CommunicationStatus.SENT: {
        CommunicationStatus.DELIVERED,
        CommunicationStatus.BOUNCED,
        CommunicationStatus.FAILED,
    },
    CommunicationStatus.DELIVERED: {
        CommunicationStatus.OPENED,
        CommunicationStatus.BOUNCED,
    },
    CommunicationStatus.OPENED: {
        CommunicationStatus.CLICKED,
        CommunicationStatus.REPLIED,
    },
    CommunicationStatus.CLICKED: {
        CommunicationStatus.REPLIED,
    },
    # Terminal states — no outbound transitions
    CommunicationStatus.REPLIED: set(),
    CommunicationStatus.BOUNCED: set(),
    CommunicationStatus.FAILED: set(),
    CommunicationStatus.OPTED_OUT: set(),
}

_TERMINAL_STATES: set[CommunicationStatus] = {
    CommunicationStatus.REPLIED,
    CommunicationStatus.BOUNCED,
    CommunicationStatus.FAILED,
    CommunicationStatus.OPTED_OUT,
}

# Map CommunicationStatus → TrackerEvent event_type
_STATUS_TO_EVENT_TYPE: dict[CommunicationStatus, str] = {
    CommunicationStatus.SENT: "delivery",
    CommunicationStatus.DELIVERED: "delivery",
    CommunicationStatus.OPENED: "open",
    CommunicationStatus.CLICKED: "click",
    CommunicationStatus.REPLIED: "read",
    CommunicationStatus.BOUNCED: "bounce",
    CommunicationStatus.FAILED: "bounce",
    CommunicationStatus.OPTED_OUT: "unsubscribe",
}


# ── Pure helpers ─────────────────────────────────────────────────────────────


def is_valid_transition(
    current_status: str | CommunicationStatus,
    new_status: str | CommunicationStatus,
) -> bool:
    """Return True if transitioning from current_status to new_status is allowed."""
    try:
        current = CommunicationStatus(current_status)
        new = CommunicationStatus(new_status)
    except ValueError:
        return False
    return new in _VALID_TRANSITIONS.get(current, set())


def get_terminal_states() -> set[CommunicationStatus]:
    """Return the set of terminal (non-transitionable) statuses."""
    return set(_TERMINAL_STATES)


# ── DB operations ─────────────────────────────────────────────────────────────


async def transition_status(
    session: AsyncSession,
    target_id: UUID,
    new_status: str | CommunicationStatus,
    channel: str,
    metadata: dict | None = None,
) -> bool:
    """Validate and apply a status transition for an OutreachTarget.

    - Validates the transition is allowed from the target's current communication_status.
    - Updates OutreachTarget.communication_status.
    - Creates a TrackerEvent for the transition.
    - Returns True on success, False if the transition is invalid or target not found.
    """
    target = await session.get(OutreachTarget, target_id)
    if not target:
        logger.warning("transition_target_not_found", target_id=str(target_id))
        return False

    current = target.communication_status or CommunicationStatus.QUEUED.value

    if not is_valid_transition(current, new_status):
        logger.warning(
            "invalid_transition",
            target_id=str(target_id),
            current=current,
            new=str(new_status),
        )
        return False

    new = CommunicationStatus(new_status)
    target.communication_status = new.value

    event_type = _STATUS_TO_EVENT_TYPE.get(new, "delivery")
    event = TrackerEvent(
        token=None,
        event_type=event_type,
        channel=channel,
        target_id=target_id,
        lead_id=target.lead_id,
        provider_event_id=f"status-{new.value}-{uuid.uuid4()}",
        event_metadata=metadata or {},
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(event)
    await session.flush()

    logger.info(
        "status_transitioned",
        target_id=str(target_id),
        from_status=current,
        to_status=new.value,
        channel=channel,
    )
    return True


async def get_communication_summary(
    session: AsyncSession,
    lead_id: UUID,
) -> dict:
    """Aggregate communication status counts across all channels for a lead.

    Returns a dict like:
    {
        "total": 10,
        "by_status": {"queued": 2, "sent": 3, "delivered": 3, "opened": 2},
        "by_channel": {"email": 7, "sms": 3},
    }
    """
    # Get all targets for this lead
    result = await session.execute(
        select(OutreachTarget).where(OutreachTarget.lead_id == lead_id)
    )
    targets = list(result.scalars().all())

    by_status: dict[str, int] = {}
    total = len(targets)

    for t in targets:
        status = t.communication_status or CommunicationStatus.QUEUED.value
        by_status[status] = by_status.get(status, 0) + 1

    # Aggregate TrackerEvents by channel
    channel_result = await session.execute(
        select(TrackerEvent.channel, func.count(TrackerEvent.id))
        .where(TrackerEvent.lead_id == lead_id)
        .group_by(TrackerEvent.channel)
    )
    by_channel: dict[str, int] = {row[0]: row[1] for row in channel_result.all()}

    return {
        "total": total,
        "by_status": by_status,
        "by_channel": by_channel,
    }


async def compute_engagement_level(
    session: AsyncSession,
    lead_id: UUID,
) -> str:
    """Compute engagement level for a lead based on TrackerEvent history.

    Rules:
    - "high"   — any click or replied/read event exists
    - "medium" — any open event exists (but no click/read)
    - "low"    — only delivery events exist
    - "none"   — no events at all
    """
    result = await session.execute(
        select(TrackerEvent.event_type)
        .where(TrackerEvent.lead_id == lead_id)
    )
    event_types = [row[0] for row in result.all()]

    if not event_types:
        return "none"

    if any(et in ("click", "read") for et in event_types):
        return "high"

    if "open" in event_types:
        return "medium"

    return "low"


# ── Helper shortcuts ──────────────────────────────────────────────────────────


async def mark_as_sent(
    session: AsyncSession,
    target_id: UUID,
    channel: str,
    external_message_id: str | None = None,
) -> bool:
    metadata = {"external_message_id": external_message_id} if external_message_id else {}
    return await transition_status(
        session, target_id, CommunicationStatus.SENT, channel, metadata
    )


async def mark_as_delivered(
    session: AsyncSession,
    target_id: UUID,
    channel: str,
) -> bool:
    return await transition_status(
        session, target_id, CommunicationStatus.DELIVERED, channel
    )


async def mark_as_opened(
    session: AsyncSession,
    target_id: UUID,
    channel: str,
) -> bool:
    return await transition_status(
        session, target_id, CommunicationStatus.OPENED, channel
    )


async def mark_as_clicked(
    session: AsyncSession,
    target_id: UUID,
    channel: str,
) -> bool:
    return await transition_status(
        session, target_id, CommunicationStatus.CLICKED, channel
    )


async def mark_as_bounced(
    session: AsyncSession,
    target_id: UUID,
    channel: str,
    bounce_type: str = "hard",
) -> bool:
    return await transition_status(
        session, target_id, CommunicationStatus.BOUNCED, channel, {"bounce_type": bounce_type}
    )


async def mark_as_failed(
    session: AsyncSession,
    target_id: UUID,
    channel: str,
    error_reason: str | None = None,
) -> bool:
    metadata = {"error_reason": error_reason} if error_reason else {}
    return await transition_status(
        session, target_id, CommunicationStatus.FAILED, channel, metadata
    )


async def mark_as_opted_out(
    session: AsyncSession,
    target_id: UUID,
    channel: str,
) -> bool:
    return await transition_status(
        session, target_id, CommunicationStatus.OPTED_OUT, channel
    )
