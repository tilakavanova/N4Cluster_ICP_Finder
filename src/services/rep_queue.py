"""Sales Rep Work Queue & Priority Engine (NIF-145, NIF-146, NIF-147)."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    RepQueueItem, RepQueueRanking,
    Restaurant, ICPScore, Lead,
    TrackerEvent, OutreachActivity, OutreachTarget,
)
from src.scoring.signals import intent_score
from src.utils.logging import get_logger

logger = get_logger("rep_queue")

# Priority weights for auto-scoring
ICP_SCORE_WEIGHT = 0.50
FIT_LABEL_BONUS = {"strong": 20.0, "good": 10.0, "moderate": 5.0, "weak": 0.0, "unknown": 0.0}
RECENCY_WEIGHT = 0.20
DEFAULT_PRIORITY = 50.0


def _compute_priority(context_data: dict | None) -> float:
    """Compute priority score from context data (ICP score, fit label, etc.)."""
    if not context_data:
        return DEFAULT_PRIORITY
    priority = 0.0
    icp_score = context_data.get("icp_score", 0.0)
    priority += float(icp_score) * ICP_SCORE_WEIGHT
    fit_label = context_data.get("fit_label", "unknown")
    priority += FIT_LABEL_BONUS.get(fit_label, 0.0)
    recency = context_data.get("engagement_recency", 0.3)
    priority += float(recency) * RECENCY_WEIGHT * 100.0
    return round(min(max(priority, 0.0), 100.0), 2)


async def get_queue(
    session: AsyncSession,
    rep_id: str,
    status: str | None = None,
    limit: int = 50,
) -> list[RepQueueItem]:
    """Get ranked queue items for a rep, ordered by priority_score descending."""
    query = select(RepQueueItem).where(RepQueueItem.rep_id == rep_id)
    if status:
        query = query.where(RepQueueItem.status == status)
    query = query.order_by(RepQueueItem.priority_score.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def add_to_queue(
    session: AsyncSession,
    rep_id: str,
    restaurant_id: UUID,
    lead_id: UUID | None = None,
    reason: str | None = None,
    context_data: dict | None = None,
) -> RepQueueItem:
    """Add an item to a rep's queue with auto-computed priority."""
    # If no context_data provided, try to build from ICP score
    if context_data is None:
        context_data = {}
        result = await session.execute(
            select(ICPScore).where(ICPScore.restaurant_id == restaurant_id)
        )
        icp = result.scalar_one_or_none()
        if icp:
            context_data = {
                "icp_score": icp.total_icp_score or 0.0,
                "fit_label": icp.fit_label or "unknown",
                "engagement_recency": icp.engagement_recency or 0.3,
            }

    priority = _compute_priority(context_data)

    item = RepQueueItem(
        rep_id=rep_id,
        restaurant_id=restaurant_id,
        lead_id=lead_id,
        priority_score=priority,
        status="pending",
        reason=reason or "Manually added",
        context_data=context_data,
    )
    session.add(item)
    await session.flush()

    logger.info("queue_item_added", rep_id=rep_id, restaurant=str(restaurant_id), priority=priority)
    return item


async def claim_item(
    session: AsyncSession,
    item_id: UUID,
    rep_id: str,
) -> RepQueueItem:
    """Claim a queue item for a rep."""
    item = await session.get(RepQueueItem, item_id)
    if not item:
        raise ValueError(f"Queue item {item_id} not found")
    if item.rep_id != rep_id:
        raise ValueError("Cannot claim another rep's queue item")
    if item.status != "pending":
        raise ValueError(f"Cannot claim item with status '{item.status}'")

    item.status = "claimed"
    item.claimed_at = datetime.now(timezone.utc)
    await session.flush()

    logger.info("queue_item_claimed", item=str(item_id), rep_id=rep_id)
    return item


async def complete_item(
    session: AsyncSession,
    item_id: UUID,
    outcome: str | None = None,
) -> RepQueueItem:
    """Mark a queue item as completed."""
    item = await session.get(RepQueueItem, item_id)
    if not item:
        raise ValueError(f"Queue item {item_id} not found")
    if item.status not in ("pending", "claimed"):
        raise ValueError(f"Cannot complete item with status '{item.status}'")

    now = datetime.now(timezone.utc)
    item.status = "completed"
    item.completed_at = now
    if outcome:
        ctx = item.context_data or {}
        ctx["outcome"] = outcome
        item.context_data = ctx
    await session.flush()

    logger.info("queue_item_completed", item=str(item_id), outcome=outcome)
    return item


async def skip_item(
    session: AsyncSession,
    item_id: UUID,
    reason: str | None = None,
) -> RepQueueItem:
    """Skip a queue item."""
    item = await session.get(RepQueueItem, item_id)
    if not item:
        raise ValueError(f"Queue item {item_id} not found")
    if item.status not in ("pending", "claimed"):
        raise ValueError(f"Cannot skip item with status '{item.status}'")

    item.status = "skipped"
    if reason:
        ctx = item.context_data or {}
        ctx["skip_reason"] = reason
        item.context_data = ctx
    await session.flush()

    logger.info("queue_item_skipped", item=str(item_id), reason=reason)
    return item


async def get_rep_ranking(
    session: AsyncSession,
    rep_id: str,
) -> RepQueueRanking:
    """Get or create rep performance ranking, recalculating stats."""
    result = await session.execute(
        select(RepQueueRanking).where(RepQueueRanking.rep_id == rep_id)
    )
    ranking = result.scalar_one_or_none()

    if not ranking:
        ranking = RepQueueRanking(rep_id=rep_id)
        session.add(ranking)

    # Recalculate stats from queue items
    total_result = await session.execute(
        select(func.count(RepQueueItem.id)).where(RepQueueItem.rep_id == rep_id)
    )
    ranking.total_items = total_result.scalar() or 0

    active_result = await session.execute(
        select(func.count(RepQueueItem.id)).where(
            and_(RepQueueItem.rep_id == rep_id, RepQueueItem.status.in_(["pending", "claimed"]))
        )
    )
    ranking.active_items = active_result.scalar() or 0

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    completed_today_result = await session.execute(
        select(func.count(RepQueueItem.id)).where(
            and_(
                RepQueueItem.rep_id == rep_id,
                RepQueueItem.status == "completed",
                RepQueueItem.completed_at >= today_start,
            )
        )
    )
    ranking.completed_today = completed_today_result.scalar() or 0

    # Average completion time (for items that have both claimed_at and completed_at)
    avg_result = await session.execute(
        select(
            func.avg(
                func.extract("epoch", RepQueueItem.completed_at)
                - func.extract("epoch", RepQueueItem.claimed_at)
            )
        ).where(
            and_(
                RepQueueItem.rep_id == rep_id,
                RepQueueItem.status == "completed",
                RepQueueItem.claimed_at.isnot(None),
                RepQueueItem.completed_at.isnot(None),
            )
        )
    )
    avg_seconds = avg_result.scalar()
    ranking.avg_completion_time_mins = round(avg_seconds / 60.0, 2) if avg_seconds else 0.0

    # Ranking score: completed_today * 10 - active_items + (1 / avg_completion_time if > 0)
    score = ranking.completed_today * 10.0
    if ranking.avg_completion_time_mins > 0:
        score += 60.0 / ranking.avg_completion_time_mins  # faster = higher score
    ranking.ranking_score = round(score, 2)

    now = datetime.now(timezone.utc)
    ranking.last_activity_at = now
    ranking.updated_at = now

    await session.flush()
    logger.info("rep_ranking_updated", rep_id=rep_id, score=ranking.ranking_score)
    return ranking


async def populate_queue(
    session: AsyncSession,
    rep_id: str,
    filters: dict | None = None,
    limit: int = 50,
) -> dict:
    """Auto-populate a rep's queue from restaurants matching filters."""
    query = (
        select(Restaurant.id, ICPScore.total_icp_score, ICPScore.fit_label, ICPScore.engagement_recency)
        .outerjoin(ICPScore, ICPScore.restaurant_id == Restaurant.id)
    )

    if filters:
        if filters.get("city"):
            query = query.where(Restaurant.city == filters["city"])
        if filters.get("state"):
            query = query.where(Restaurant.state == filters["state"])
        if filters.get("zip_code"):
            query = query.where(Restaurant.zip_code == filters["zip_code"])
        if filters.get("min_icp_score") is not None:
            query = query.where(ICPScore.total_icp_score >= filters["min_icp_score"])
        if filters.get("fit_label"):
            query = query.where(ICPScore.fit_label == filters["fit_label"])
        if filters.get("is_chain") is not None:
            query = query.where(Restaurant.is_chain == filters["is_chain"])

    # Exclude restaurants already in this rep's queue (pending/claimed)
    existing_subq = (
        select(RepQueueItem.restaurant_id)
        .where(
            and_(
                RepQueueItem.rep_id == rep_id,
                RepQueueItem.status.in_(["pending", "claimed"]),
            )
        )
    )
    query = query.where(Restaurant.id.notin_(existing_subq))

    # Order by ICP score descending, limit
    query = query.order_by(ICPScore.total_icp_score.desc().nullslast()).limit(limit)

    result = await session.execute(query)
    rows = result.all()

    added = 0
    for row in rows:
        restaurant_id, icp_score, fit_label, engagement_recency = row
        context = {
            "icp_score": icp_score or 0.0,
            "fit_label": fit_label or "unknown",
            "engagement_recency": engagement_recency or 0.3,
        }
        await add_to_queue(
            session,
            rep_id=rep_id,
            restaurant_id=restaurant_id,
            reason="Auto-populated from restaurant filters",
            context_data=context,
        )
        added += 1

    await session.flush()
    logger.info("queue_populated", rep_id=rep_id, added=added, filters=filters)
    return {"rep_id": rep_id, "added": added, "filters": filters}


# -- Next-Best-Action (NIF-242) -----------------------------------------------

# Intent label -> recommended action mapping
INTENT_ACTION_MAP: dict[str, str] = {
    "high_intent": "Schedule demo",
    "evaluating": "Send case study",
    "researching": "Follow up email",
    "aware": "Follow up email",
    "cold": "Re-engage sequence",
    "unknown": "Re-engage sequence",
}


async def _fetch_engagement_data(
    session: AsyncSession,
    restaurant_id: UUID,
    lead_id: UUID | None,
) -> tuple[list[dict], list[dict]]:
    """Fetch tracker events and outreach activities for a restaurant/lead."""
    tracker_events: list[dict] = []
    outreach_activities: list[dict] = []

    # Fetch tracker events via lead_id if available
    if lead_id:
        result = await session.execute(
            select(TrackerEvent).where(TrackerEvent.lead_id == lead_id)
        )
        for ev in result.scalars().all():
            tracker_events.append({
                "event_type": ev.event_type,
                "channel": ev.channel,
                "occurred_at": ev.occurred_at,
                "event_metadata": ev.event_metadata or {},
            })

    # Fetch outreach activities via outreach targets for this restaurant
    target_q = select(OutreachTarget.id).where(
        OutreachTarget.restaurant_id == restaurant_id
    )
    result = await session.execute(
        select(OutreachActivity).where(
            OutreachActivity.target_id.in_(target_q)
        ).order_by(OutreachActivity.performed_at.desc())
    )
    for act in result.scalars().all():
        outreach_activities.append({
            "activity_type": act.activity_type,
            "outcome": act.outcome,
            "performed_at": act.performed_at,
        })

    return tracker_events, outreach_activities


def _determine_action(
    intent_label: str,
    last_activity_type: str | None,
    days_since_contact: float | None,
    icp_score: float,
    fit_label: str,
) -> dict:
    """Determine the recommended next action based on signals."""
    action = INTENT_ACTION_MAP.get(intent_label, "Re-engage sequence")

    # Override: if high ICP score + strong fit but cold, escalate
    if fit_label in ("strong", "good") and icp_score >= 70.0 and intent_label == "cold":
        action = "Priority re-engage — high-value dormant lead"

    # Override: if recently contacted (< 3 days) and evaluating, wait
    if days_since_contact is not None and days_since_contact < 3.0 and intent_label == "evaluating":
        action = "Wait — evaluating, contacted recently"

    return {
        "recommended_action": action,
        "intent_label": intent_label,
        "last_activity_type": last_activity_type,
        "days_since_contact": round(days_since_contact, 1) if days_since_contact is not None else None,
        "icp_score": icp_score,
        "fit_label": fit_label,
    }


async def _enrich_item_with_action(
    session: AsyncSession,
    item: RepQueueItem,
) -> dict:
    """Compute next-best-action for a single queue item and return enrichment data."""
    ctx = item.context_data or {}
    icp_score = float(ctx.get("icp_score", 0.0))
    fit_label = ctx.get("fit_label", "unknown")

    tracker_events, outreach_activities = await _fetch_engagement_data(
        session, item.restaurant_id, item.lead_id,
    )

    # Compute intent score
    score_val, intent_label = intent_score(tracker_events, outreach_activities)

    # Determine last activity and days since contact
    last_activity_type: str | None = None
    days_since_contact: float | None = None
    now = datetime.now(timezone.utc)

    if outreach_activities:
        latest = outreach_activities[0]  # already sorted desc
        last_activity_type = latest.get("activity_type")
        performed = latest.get("performed_at")
        if isinstance(performed, datetime):
            if performed.tzinfo is None:
                performed = performed.replace(tzinfo=timezone.utc)
            days_since_contact = (now - performed).total_seconds() / 86400.0

    action_data = _determine_action(
        intent_label, last_activity_type, days_since_contact,
        icp_score, fit_label,
    )
    action_data["intent_score"] = score_val

    return action_data


async def get_next_best_action(
    session: AsyncSession,
    rep_id: str,
) -> dict | None:
    """Return the highest priority unclaimed item with a recommended action (NIF-242)."""
    result = await session.execute(
        select(RepQueueItem)
        .where(
            and_(
                RepQueueItem.rep_id == rep_id,
                RepQueueItem.status == "pending",
            )
        )
        .order_by(RepQueueItem.priority_score.desc())
        .limit(1)
    )
    item = result.scalar_one_or_none()
    if not item:
        return None

    action_data = await _enrich_item_with_action(session, item)

    # Store action in context_data
    ctx = dict(item.context_data or {})
    ctx["next_action"] = action_data
    item.context_data = ctx
    await session.flush()

    logger.info(
        "next_best_action",
        rep_id=rep_id,
        item=str(item.id),
        action=action_data["recommended_action"],
    )

    return {
        "id": str(item.id),
        "rep_id": item.rep_id,
        "restaurant_id": str(item.restaurant_id),
        "lead_id": str(item.lead_id) if item.lead_id else None,
        "priority_score": item.priority_score,
        "status": item.status,
        "reason": item.reason,
        "context_data": item.context_data,
        "next_action": action_data,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


async def enrich_queue_with_actions(
    session: AsyncSession,
    rep_id: str,
) -> dict:
    """Enrich all pending queue items with recommended next actions (NIF-242)."""
    items = await get_queue(session, rep_id, status="pending")
    enriched = 0

    for item in items:
        action_data = await _enrich_item_with_action(session, item)
        ctx = dict(item.context_data or {})
        ctx["next_action"] = action_data
        item.context_data = ctx
        enriched += 1

    await session.flush()
    logger.info("queue_enriched", rep_id=rep_id, enriched=enriched)
    return {"rep_id": rep_id, "enriched": enriched}
