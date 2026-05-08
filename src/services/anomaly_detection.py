"""Campaign anomaly detection with auto-pause (NIF-267).

Monitors active campaigns for anomalous metrics (bounce rate, complaint rate,
volume drops) and auto-pauses campaigns that exceed safety thresholds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    OutreachCampaign, OutreachTarget, TrackerEvent, CampaignAnomalyLog,
)
from src.utils.logging import get_logger

logger = get_logger("anomaly_detection")

# ── Thresholds ───────────────────────────────────────────────────

BOUNCE_RATE_THRESHOLD = 0.10       # 10%
COMPLAINT_RATE_THRESHOLD = 0.01    # 1%
VOLUME_DROP_THRESHOLD = 0.50       # 50% drop from previous period

# Look-back window for metrics
LOOKBACK_HOURS = 24


async def _get_campaign_metrics(
    session: AsyncSession,
    campaign_id: UUID,
    hours: int = LOOKBACK_HOURS,
) -> dict:
    """Compute bounce rate, complaint rate, and send volume for the window."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Total sends
    sends_q = (
        select(func.count())
        .select_from(TrackerEvent)
        .where(
            TrackerEvent.campaign_id == campaign_id,
            TrackerEvent.event_type == "delivery",
            TrackerEvent.occurred_at >= since,
        )
    )
    total_sends = (await session.execute(sends_q)).scalar() or 0

    # Bounces
    bounces_q = (
        select(func.count())
        .select_from(TrackerEvent)
        .where(
            TrackerEvent.campaign_id == campaign_id,
            TrackerEvent.event_type == "bounce",
            TrackerEvent.occurred_at >= since,
        )
    )
    bounces = (await session.execute(bounces_q)).scalar() or 0

    # Complaints
    complaints_q = (
        select(func.count())
        .select_from(TrackerEvent)
        .where(
            TrackerEvent.campaign_id == campaign_id,
            TrackerEvent.event_type == "complaint",
            TrackerEvent.occurred_at >= since,
        )
    )
    complaints = (await session.execute(complaints_q)).scalar() or 0

    bounce_rate = bounces / total_sends if total_sends > 0 else 0.0
    complaint_rate = complaints / total_sends if total_sends > 0 else 0.0

    return {
        "total_sends": total_sends,
        "bounces": bounces,
        "complaints": complaints,
        "bounce_rate": bounce_rate,
        "complaint_rate": complaint_rate,
    }


async def _get_previous_volume(
    session: AsyncSession,
    campaign_id: UUID,
    hours: int = LOOKBACK_HOURS,
) -> int:
    """Get send volume from the period before the current look-back window."""
    end = datetime.now(timezone.utc) - timedelta(hours=hours)
    start = end - timedelta(hours=hours)
    q = (
        select(func.count())
        .select_from(TrackerEvent)
        .where(
            TrackerEvent.campaign_id == campaign_id,
            TrackerEvent.event_type == "delivery",
            TrackerEvent.occurred_at >= start,
            TrackerEvent.occurred_at < end,
        )
    )
    return (await session.execute(q)).scalar() or 0


async def check_campaign_health(
    session: AsyncSession,
    campaign_id: UUID,
) -> list[dict]:
    """Check a campaign for anomalies. Returns list of detected anomalies."""
    metrics = await _get_campaign_metrics(session, campaign_id)
    anomalies: list[dict] = []

    # High bounce rate
    if metrics["total_sends"] > 0 and metrics["bounce_rate"] > BOUNCE_RATE_THRESHOLD:
        anomalies.append({
            "type": "high_bounce",
            "metric_value": metrics["bounce_rate"],
            "threshold": BOUNCE_RATE_THRESHOLD,
            "details": {"bounces": metrics["bounces"], "sends": metrics["total_sends"]},
        })

    # High complaint rate
    if metrics["total_sends"] > 0 and metrics["complaint_rate"] > COMPLAINT_RATE_THRESHOLD:
        anomalies.append({
            "type": "high_complaint",
            "metric_value": metrics["complaint_rate"],
            "threshold": COMPLAINT_RATE_THRESHOLD,
            "details": {"complaints": metrics["complaints"], "sends": metrics["total_sends"]},
        })

    # Volume drop
    previous_volume = await _get_previous_volume(session, campaign_id)
    if previous_volume > 10:  # Only check if previous period had meaningful volume
        current = metrics["total_sends"]
        drop_ratio = 1.0 - (current / previous_volume) if previous_volume > 0 else 0.0
        if drop_ratio > VOLUME_DROP_THRESHOLD:
            anomalies.append({
                "type": "volume_drop",
                "metric_value": drop_ratio,
                "threshold": VOLUME_DROP_THRESHOLD,
                "details": {"current_volume": current, "previous_volume": previous_volume},
            })

    return anomalies


async def auto_pause_campaign(
    session: AsyncSession,
    campaign_id: UUID,
    reason: str = "anomaly_detected",
) -> CampaignAnomalyLog | None:
    """Pause a campaign and log the reason. Returns the anomaly log entry."""
    campaign = await session.get(OutreachCampaign, campaign_id)
    if not campaign or campaign.status != "active":
        return None

    campaign.status = "paused"
    await session.flush()
    logger.warning("campaign_auto_paused", campaign_id=str(campaign_id), reason=reason)
    return campaign


async def check_and_pause_if_needed(
    session: AsyncSession,
    campaign_id: UUID,
) -> list[CampaignAnomalyLog]:
    """Check campaign health; auto-pause and log if anomalies found."""
    anomalies = await check_campaign_health(session, campaign_id)
    logs: list[CampaignAnomalyLog] = []

    for anomaly in anomalies:
        # Log the anomaly
        log_entry = CampaignAnomalyLog(
            campaign_id=campaign_id,
            anomaly_type=anomaly["type"],
            metric_value=anomaly["metric_value"],
            threshold=anomaly["threshold"],
            action_taken="paused",
            details=anomaly.get("details", {}),
        )
        session.add(log_entry)
        logs.append(log_entry)

    if anomalies:
        await auto_pause_campaign(session, campaign_id, reason=anomalies[0]["type"])

    await session.flush()
    return logs


async def check_all_campaigns(session: AsyncSession) -> dict:
    """Check all active campaigns for anomalies. Returns summary."""
    result = await session.execute(
        select(OutreachCampaign).where(OutreachCampaign.status == "active")
    )
    campaigns = list(result.scalars().all())

    paused_count = 0
    anomaly_count = 0

    for campaign in campaigns:
        logs = await check_and_pause_if_needed(session, campaign.id)
        if logs:
            paused_count += 1
            anomaly_count += len(logs)

    logger.info(
        "campaign_health_check_complete",
        checked=len(campaigns),
        paused=paused_count,
        anomalies=anomaly_count,
    )
    return {
        "checked": len(campaigns),
        "paused": paused_count,
        "anomalies_found": anomaly_count,
    }
