"""Celery tasks for engagement score aggregation (NIF-240)."""

import asyncio
from datetime import datetime, timedelta, timezone

from src.tasks.celery_app import celery_app
from src.utils.logging import get_logger

logger = get_logger("tasks.engagement")


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _aggregate_engagement_scores_async(hours: int = 24):
    """Query recent TrackerEvents, compute engagement scores, update ICPScore."""
    from sqlalchemy import select, func
    from src.db.session import async_session
    from src.db.models import TrackerEvent, OutreachActivity, OutreachTarget, ICPScore, Lead
    from src.scoring.signals import communication_engagement_score

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    updated = 0

    async with async_session() as session:
        # Find restaurant IDs that have recent tracker events via lead -> restaurant
        recent_leads_q = (
            select(Lead.restaurant_id, Lead.id)
            .join(TrackerEvent, TrackerEvent.lead_id == Lead.id)
            .where(TrackerEvent.occurred_at >= cutoff)
            .where(Lead.restaurant_id.isnot(None))
            .distinct()
        )
        result = await session.execute(recent_leads_q)
        restaurant_lead_pairs = result.all()

        restaurant_leads: dict[str, list[str]] = {}
        for rest_id, lead_id in restaurant_lead_pairs:
            restaurant_leads.setdefault(str(rest_id), []).append(str(lead_id))

        for restaurant_id, lead_ids in restaurant_leads.items():
            try:
                # Get tracker events for these leads
                events_result = await session.execute(
                    select(
                        TrackerEvent.event_type,
                    ).where(TrackerEvent.lead_id.in_(lead_ids))
                )
                tracker_events = [{"event_type": r[0]} for r in events_result.all()]

                # Get outreach activities via targets linked to leads
                activities_result = await session.execute(
                    select(
                        OutreachActivity.activity_type,
                        OutreachActivity.outcome,
                    )
                    .join(OutreachTarget, OutreachTarget.id == OutreachActivity.target_id)
                    .where(OutreachTarget.lead_id.in_(lead_ids))
                )
                activities = [
                    {"activity_type": r[0], "outcome": r[1]}
                    for r in activities_result.all()
                ]

                score = communication_engagement_score(tracker_events, activities)
                if score is not None:
                    # Update ICPScore
                    icp_result = await session.execute(
                        select(ICPScore).where(
                            ICPScore.restaurant_id == restaurant_id
                        )
                    )
                    icp_score = icp_result.scalar_one_or_none()
                    if icp_score:
                        icp_score.communication_engagement = score
                        updated += 1
            except Exception as exc:
                logger.warning(
                    "engagement_aggregate_error",
                    restaurant_id=restaurant_id,
                    error=str(exc),
                )

        await session.commit()

    logger.info("engagement_aggregation_complete", updated=updated)
    return updated


async def _recalculate_all_engagement_async():
    """Full recalculation of engagement scores for all restaurants with communication data."""
    from sqlalchemy import select, func
    from src.db.session import async_session
    from src.db.models import TrackerEvent, OutreachActivity, OutreachTarget, ICPScore, Lead
    from src.scoring.signals import communication_engagement_score

    updated = 0

    async with async_session() as session:
        # Find all restaurant IDs that have any tracker events
        rest_ids_result = await session.execute(
            select(Lead.restaurant_id)
            .join(TrackerEvent, TrackerEvent.lead_id == Lead.id)
            .where(Lead.restaurant_id.isnot(None))
            .distinct()
        )
        restaurant_ids = [str(r[0]) for r in rest_ids_result.all()]

        for restaurant_id in restaurant_ids:
            try:
                # Get all leads for this restaurant
                leads_result = await session.execute(
                    select(Lead.id).where(Lead.restaurant_id == restaurant_id)
                )
                lead_ids = [str(r[0]) for r in leads_result.all()]

                if not lead_ids:
                    continue

                # Get tracker events
                events_result = await session.execute(
                    select(
                        TrackerEvent.event_type,
                    ).where(TrackerEvent.lead_id.in_(lead_ids))
                )
                tracker_events = [{"event_type": r[0]} for r in events_result.all()]

                # Get outreach activities
                activities_result = await session.execute(
                    select(
                        OutreachActivity.activity_type,
                        OutreachActivity.outcome,
                    )
                    .join(OutreachTarget, OutreachTarget.id == OutreachActivity.target_id)
                    .where(OutreachTarget.lead_id.in_(lead_ids))
                )
                activities = [
                    {"activity_type": r[0], "outcome": r[1]}
                    for r in activities_result.all()
                ]

                score = communication_engagement_score(tracker_events, activities)
                if score is not None:
                    icp_result = await session.execute(
                        select(ICPScore).where(
                            ICPScore.restaurant_id == restaurant_id
                        )
                    )
                    icp_score = icp_result.scalar_one_or_none()
                    if icp_score:
                        icp_score.communication_engagement = score
                        updated += 1
            except Exception as exc:
                logger.warning(
                    "engagement_recalc_error",
                    restaurant_id=restaurant_id,
                    error=str(exc),
                )

        await session.commit()

    logger.info("engagement_full_recalc_complete", updated=updated)
    return updated


@celery_app.task(name="src.tasks.engagement_tasks.aggregate_engagement_scores")
def aggregate_engagement_scores():
    """Hourly task: aggregate engagement scores from recent TrackerEvents."""
    return _run_async(_aggregate_engagement_scores_async(hours=24))


@celery_app.task(name="src.tasks.engagement_tasks.recalculate_engagement_signal")
def recalculate_engagement_signal():
    """Daily task: full recalculation of engagement scores for all restaurants."""
    return _run_async(_recalculate_all_engagement_async())
