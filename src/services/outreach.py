"""Outreach Orchestration & Campaign Engine service (NIF-133 through NIF-136)."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    OutreachCampaign, OutreachTarget, OutreachActivity, OutreachPerformance,
    Restaurant, ICPScore, Lead,
)
from src.utils.logging import get_logger

logger = get_logger("outreach")

VALID_CAMPAIGN_TYPES = {"email", "call", "sms", "multi"}
VALID_CAMPAIGN_STATUSES = {"draft", "active", "paused", "completed"}
VALID_TARGET_STATUSES = {"pending", "contacted", "responded", "converted", "skipped"}
VALID_ACTIVITY_TYPES = {"email_sent", "call_made", "sms_sent", "meeting", "note"}
VALID_OUTCOMES = {"no_answer", "interested", "not_interested", "callback", "converted"}


# ── Campaign CRUD ────────────────────────────────────────────────


async def create_campaign(
    session: AsyncSession,
    name: str,
    campaign_type: str = "email",
    target_criteria: dict | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    created_by: str = "system",
) -> OutreachCampaign:
    """Create a new outreach campaign."""
    if campaign_type not in VALID_CAMPAIGN_TYPES:
        raise ValueError(f"Invalid campaign_type: {campaign_type}. Must be one of {VALID_CAMPAIGN_TYPES}")

    campaign = OutreachCampaign(
        name=name,
        campaign_type=campaign_type,
        status="draft",
        target_criteria=target_criteria or {},
        start_date=start_date,
        end_date=end_date,
        created_by=created_by,
    )
    session.add(campaign)
    await session.flush()
    logger.info("campaign_created", campaign_id=str(campaign.id), name=name, type=campaign_type)
    return campaign


async def get_campaign(session: AsyncSession, campaign_id: UUID) -> OutreachCampaign | None:
    return await session.get(OutreachCampaign, campaign_id)


async def list_campaigns(
    session: AsyncSession,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[OutreachCampaign]:
    query = select(OutreachCampaign).order_by(OutreachCampaign.created_at.desc())
    if status:
        query = query.where(OutreachCampaign.status == status)
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all())


async def update_campaign(
    session: AsyncSession,
    campaign_id: UUID,
    **kwargs,
) -> OutreachCampaign:
    campaign = await session.get(OutreachCampaign, campaign_id)
    if not campaign:
        raise ValueError(f"Campaign {campaign_id} not found")

    if "status" in kwargs and kwargs["status"] not in VALID_CAMPAIGN_STATUSES:
        raise ValueError(f"Invalid status: {kwargs['status']}")
    if "campaign_type" in kwargs and kwargs["campaign_type"] not in VALID_CAMPAIGN_TYPES:
        raise ValueError(f"Invalid campaign_type: {kwargs['campaign_type']}")

    for field, value in kwargs.items():
        if value is not None and hasattr(campaign, field):
            setattr(campaign, field, value)

    await session.flush()
    logger.info("campaign_updated", campaign_id=str(campaign_id))
    return campaign


async def delete_campaign(session: AsyncSession, campaign_id: UUID) -> bool:
    campaign = await session.get(OutreachCampaign, campaign_id)
    if not campaign:
        return False
    await session.delete(campaign)
    await session.flush()
    logger.info("campaign_deleted", campaign_id=str(campaign_id))
    return True


# ── Target Selection ─────────────────────────────────────────────


async def select_targets(
    session: AsyncSession,
    campaign_id: UUID,
    min_icp_score: float | None = None,
    zip_codes: list[str] | None = None,
    cuisines: list[str] | None = None,
    limit: int = 200,
) -> list[OutreachTarget]:
    """Select restaurants as outreach targets based on ICP score, neighborhood, cuisine."""
    campaign = await session.get(OutreachCampaign, campaign_id)
    if not campaign:
        raise ValueError(f"Campaign {campaign_id} not found")

    query = (
        select(Restaurant, ICPScore)
        .outerjoin(ICPScore, ICPScore.restaurant_id == Restaurant.id)
        .order_by(ICPScore.total_icp_score.desc().nullslast())
    )

    if min_icp_score is not None:
        query = query.where(ICPScore.total_icp_score >= min_icp_score)

    if zip_codes:
        query = query.where(Restaurant.zip_code.in_(zip_codes))

    if cuisines:
        query = query.where(Restaurant.cuisine_type.overlap(cuisines))

    query = query.limit(limit)
    result = await session.execute(query)
    rows = result.all()

    targets = []
    for idx, (restaurant, icp_score) in enumerate(rows):
        # Check if this restaurant is already a target in this campaign
        existing = await session.execute(
            select(OutreachTarget).where(
                and_(
                    OutreachTarget.campaign_id == campaign_id,
                    OutreachTarget.restaurant_id == restaurant.id,
                )
            )
        )
        if existing.scalar_one_or_none():
            continue

        priority = len(rows) - idx  # higher ICP score = higher priority
        target = OutreachTarget(
            campaign_id=campaign_id,
            restaurant_id=restaurant.id,
            status="pending",
            priority=priority,
        )
        session.add(target)
        targets.append(target)

    await session.flush()
    logger.info("targets_selected", campaign_id=str(campaign_id), count=len(targets))
    return targets


async def add_target(
    session: AsyncSession,
    campaign_id: UUID,
    restaurant_id: UUID,
    lead_id: UUID | None = None,
    priority: int = 0,
    assigned_to: str | None = None,
) -> OutreachTarget:
    """Manually add a single target to a campaign."""
    campaign = await session.get(OutreachCampaign, campaign_id)
    if not campaign:
        raise ValueError(f"Campaign {campaign_id} not found")

    restaurant = await session.get(Restaurant, restaurant_id)
    if not restaurant:
        raise ValueError(f"Restaurant {restaurant_id} not found")

    target = OutreachTarget(
        campaign_id=campaign_id,
        restaurant_id=restaurant_id,
        lead_id=lead_id,
        priority=priority,
        assigned_to=assigned_to,
        status="pending",
    )
    session.add(target)
    await session.flush()
    return target


async def list_targets(
    session: AsyncSession,
    campaign_id: UUID,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[OutreachTarget]:
    query = (
        select(OutreachTarget)
        .where(OutreachTarget.campaign_id == campaign_id)
        .order_by(OutreachTarget.priority.desc())
    )
    if status:
        query = query.where(OutreachTarget.status == status)
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all())


async def update_target_status(
    session: AsyncSession,
    target_id: UUID,
    status: str,
) -> OutreachTarget:
    if status not in VALID_TARGET_STATUSES:
        raise ValueError(f"Invalid target status: {status}")

    target = await session.get(OutreachTarget, target_id)
    if not target:
        raise ValueError(f"Target {target_id} not found")

    target.status = status
    await session.flush()
    return target


# ── Activity Logging ─────────────────────────────────────────────


async def log_activity(
    session: AsyncSession,
    target_id: UUID,
    activity_type: str,
    outcome: str | None = None,
    notes: str | None = None,
    performed_by: str = "system",
) -> OutreachActivity:
    """Log an outreach activity against a target."""
    if activity_type not in VALID_ACTIVITY_TYPES:
        raise ValueError(f"Invalid activity_type: {activity_type}. Must be one of {VALID_ACTIVITY_TYPES}")
    if outcome and outcome not in VALID_OUTCOMES:
        raise ValueError(f"Invalid outcome: {outcome}. Must be one of {VALID_OUTCOMES}")

    target = await session.get(OutreachTarget, target_id)
    if not target:
        raise ValueError(f"Target {target_id} not found")

    activity = OutreachActivity(
        target_id=target_id,
        activity_type=activity_type,
        outcome=outcome,
        notes=notes,
        performed_by=performed_by,
    )
    session.add(activity)

    # Auto-advance target status based on activity
    if target.status == "pending":
        target.status = "contacted"
    if outcome == "interested" and target.status in ("pending", "contacted"):
        target.status = "responded"
    if outcome == "converted":
        target.status = "converted"

    await session.flush()
    logger.info("activity_logged", target_id=str(target_id), type=activity_type, outcome=outcome)
    return activity


async def list_activities(
    session: AsyncSession,
    target_id: UUID,
) -> list[OutreachActivity]:
    result = await session.execute(
        select(OutreachActivity)
        .where(OutreachActivity.target_id == target_id)
        .order_by(OutreachActivity.performed_at.desc())
    )
    return list(result.scalars().all())


# ── Performance Calculation ──────────────────────────────────────


async def calculate_performance(
    session: AsyncSession,
    campaign_id: UUID,
) -> OutreachPerformance:
    """Calculate and upsert performance summary for a campaign."""
    campaign = await session.get(OutreachCampaign, campaign_id)
    if not campaign:
        raise ValueError(f"Campaign {campaign_id} not found")

    # Count targets by status
    total_result = await session.execute(
        select(func.count(OutreachTarget.id)).where(OutreachTarget.campaign_id == campaign_id)
    )
    total_targets = total_result.scalar() or 0

    contacted_result = await session.execute(
        select(func.count(OutreachTarget.id)).where(
            and_(
                OutreachTarget.campaign_id == campaign_id,
                OutreachTarget.status.in_(["contacted", "responded", "converted"]),
            )
        )
    )
    contacted = contacted_result.scalar() or 0

    responded_result = await session.execute(
        select(func.count(OutreachTarget.id)).where(
            and_(
                OutreachTarget.campaign_id == campaign_id,
                OutreachTarget.status.in_(["responded", "converted"]),
            )
        )
    )
    responded = responded_result.scalar() or 0

    converted_result = await session.execute(
        select(func.count(OutreachTarget.id)).where(
            and_(
                OutreachTarget.campaign_id == campaign_id,
                OutreachTarget.status == "converted",
            )
        )
    )
    converted = converted_result.scalar() or 0

    response_rate = (responded / contacted * 100) if contacted > 0 else 0.0
    conversion_rate = (converted / total_targets * 100) if total_targets > 0 else 0.0

    # Upsert
    existing = await session.execute(
        select(OutreachPerformance).where(OutreachPerformance.campaign_id == campaign_id)
    )
    perf = existing.scalar_one_or_none()
    if perf:
        perf.total_targets = total_targets
        perf.contacted = contacted
        perf.responded = responded
        perf.converted = converted
        perf.response_rate = round(response_rate, 2)
        perf.conversion_rate = round(conversion_rate, 2)
        perf.last_calculated_at = datetime.now(timezone.utc)
    else:
        perf = OutreachPerformance(
            campaign_id=campaign_id,
            total_targets=total_targets,
            contacted=contacted,
            responded=responded,
            converted=converted,
            response_rate=round(response_rate, 2),
            conversion_rate=round(conversion_rate, 2),
        )
        session.add(perf)

    await session.flush()
    logger.info(
        "performance_calculated",
        campaign_id=str(campaign_id),
        total=total_targets,
        contacted=contacted,
        responded=responded,
        converted=converted,
    )
    return perf
