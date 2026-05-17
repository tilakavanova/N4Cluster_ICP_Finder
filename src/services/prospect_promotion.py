"""Promote restaurants from the Prospect Finder into Leads + outreach campaigns."""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ICPScore, Lead, Restaurant
from src.utils.logging import get_logger

logger = get_logger("prospect_promotion")


@dataclass
class NewCampaignSpec:
    name: str
    campaign_type: str  # email|call|sms|multi
    status: str  # draft|active


@dataclass
class PromotionResult:
    promoted: int = 0
    skipped_already_lead: int = 0
    failed: list[dict] = field(default_factory=list)
    lead_ids: list[str] = field(default_factory=list)
    campaign_id: str | None = None
    qualification_task_ids: list[str] = field(default_factory=list)
    reused_qualifications: int = 0
    data_warnings: list[dict] = field(default_factory=list)


async def promote_prospects(
    session: AsyncSession,
    restaurant_ids: list[UUID],
    campaign_id: UUID | None,
    new_campaign: NewCampaignSpec | None,
    owner: str | None,
    notes: str | None,
    actor: str,
) -> PromotionResult:
    """Orchestrate Lead creation + qualification dispatch + optional campaign attach."""
    if campaign_id and new_campaign:
        raise ValueError("Provide campaign_id OR new_campaign, not both")

    result = PromotionResult()

    for rid in restaurant_ids:
        try:
            await _promote_one(session, rid, owner, actor, result)
        except Exception as exc:
            logger.exception("promote_one_failed", restaurant_id=str(rid))
            result.failed.append({"restaurant_id": str(rid), "reason": str(exc)})

    return result


async def _promote_one(
    session: AsyncSession,
    restaurant_id: UUID,
    owner: str | None,
    actor: str,
    result: PromotionResult,
) -> None:
    # Dedup check — pre-flight (race-safe via the UNIQUE INDEX added in Task A1)
    existing_lead = (
        await session.execute(select(Lead).where(Lead.restaurant_id == restaurant_id))
    ).scalar_one_or_none()
    if existing_lead is not None:
        result.skipped_already_lead += 1
        return

    # Load restaurant + latest ICP score for snapshot fields
    restaurant = await session.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ValueError(f"Restaurant {restaurant_id} not found")

    icp = (
        await session.execute(
            select(ICPScore)
            .where(ICPScore.restaurant_id == restaurant_id)
            .order_by(ICPScore.scored_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    lead = Lead(
        first_name=None,
        last_name=None,
        email=None,
        company=restaurant.name,
        source="prospect_finder",
        status="new",
        lifecycle_stage="new",
        owner=owner or actor,
        restaurant_id=restaurant_id,
        matched_restaurant_name=restaurant.name,
        icp_score_id=icp.id if icp else None,
        icp_total_score=icp.total_icp_score if icp else None,
        icp_fit_label=icp.fit_label if icp else None,
        is_independent=getattr(icp, "is_independent", None),
        has_delivery=getattr(icp, "has_delivery", None),
    )
    session.add(lead)

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # Race: another request created the Lead between our pre-flight check and insert.
        re_existing = (
            await session.execute(select(Lead).where(Lead.restaurant_id == restaurant_id))
        ).scalar_one_or_none()
        if re_existing is not None:
            result.skipped_already_lead += 1
            return
        raise

    result.promoted += 1
    result.lead_ids.append(str(lead.id))

    # Data-quality warning
    if not restaurant.phone and not getattr(restaurant, "email", None):
        result.data_warnings.append(
            {"restaurant_id": str(restaurant_id), "warning": "no_contact_info"}
        )

    logger.info("prospect_promoted", restaurant_id=str(restaurant_id), lead_id=str(lead.id))
