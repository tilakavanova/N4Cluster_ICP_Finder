"""Promote restaurants from the Prospect Finder into Leads + outreach campaigns."""

from dataclasses import dataclass, field
from datetime import UTC
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ICPScore, Lead, OutreachTarget, Restaurant
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

    # Inline campaign creation
    effective_campaign_id = campaign_id
    if new_campaign is not None:
        from src.services.outreach import create_campaign as _create_campaign

        campaign = await _create_campaign(
            session,
            name=new_campaign.name,
            campaign_type=new_campaign.campaign_type,
            status=new_campaign.status,
            created_by=actor,
        )
        effective_campaign_id = campaign.id

    if effective_campaign_id:
        result.campaign_id = str(effective_campaign_id)

    for rid in restaurant_ids:
        try:
            async with session.begin_nested():
                await _promote_one(session, rid, effective_campaign_id, owner, actor, result)
        except IntegrityError:
            # Race: another request created a Lead for this restaurant between our
            # pre-flight check and the flush inside _promote_one. The SAVEPOINT was
            # auto-rolled-back when the begin_nested() block exited with the exception,
            # so the outer transaction (and previously-promoted Leads) remain intact.
            # Re-check for the existing Lead now that the conflicting row is visible.
            re_existing = (
                await session.execute(select(Lead).where(Lead.restaurant_id == rid))
            ).scalar_one_or_none()
            if re_existing is not None:
                result.skipped_already_lead += 1
            else:
                logger.exception("promote_one_integrity_error", restaurant_id=str(rid))
                result.failed.append({"restaurant_id": str(rid), "reason": "integrity_error"})
        except (ValueError, SQLAlchemyError) as exc:
            logger.exception("promote_one_failed", restaurant_id=str(rid))
            result.failed.append({"restaurant_id": str(rid), "reason": str(exc)})

    return result


async def _promote_one(
    session: AsyncSession,
    restaurant_id: UUID,
    campaign_id: UUID | None,
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
        is_independent=icp.is_independent if icp else None,
        has_delivery=icp.has_delivery if icp else None,
    )
    session.add(lead)
    # IntegrityError on flush propagates to the caller, where the surrounding
    # session.begin_nested() SAVEPOINT auto-rolls-back and the race is recovered.
    await session.flush()

    result.promoted += 1
    result.lead_ids.append(str(lead.id))

    # Attach to campaign if requested. Scoped to its own SAVEPOINT so a dup-attach
    # (UNIQUE(campaign_id, restaurant_id)) only rolls back the target insert,
    # leaving the just-created Lead intact within the outer savepoint.
    if campaign_id is not None:
        try:
            async with session.begin_nested():
                target = OutreachTarget(
                    campaign_id=campaign_id,
                    restaurant_id=restaurant_id,
                    lead_id=lead.id,
                    status="pending",
                    communication_status="queued",
                    priority=int(icp.total_icp_score) if icp and icp.total_icp_score else 0,
                )
                session.add(target)
        except IntegrityError:
            # Dup attach — benign no-op; the inner SAVEPOINT auto-rolled-back the
            # OutreachTarget insert without affecting the Lead row.
            logger.info(
                "campaign_attach_skipped_duplicate",
                restaurant_id=str(restaurant_id),
                campaign_id=str(campaign_id),
            )

    # Data-quality warning — Restaurant has no email column, only phone.
    if not restaurant.phone:
        result.data_warnings.append({"restaurant_id": str(restaurant_id), "warning": "no_phone"})

    # Qualification dispatch — reuse a non-expired qualified/needs_review result
    # if present, otherwise dispatch a Celery task to qualify this restaurant.
    from datetime import datetime

    from src.services.qualification import get_latest_qualification

    existing_qual = await get_latest_qualification(session, restaurant_id)
    reuse = (
        existing_qual is not None
        and existing_qual.qualification_status in {"qualified", "needs_review"}
        and (existing_qual.expires_at is None or existing_qual.expires_at > datetime.now(UTC))
    )
    if reuse:
        result.reused_qualifications += 1
        # If reused result is qualified, advance the Lead's lifecycle stage now
        # (mirrors what qualify_restaurant_task would do upon a qualified verdict).
        if existing_qual.qualification_status == "qualified":
            lead.lifecycle_stage = "qualified"
            lead.status = "qualified"
    else:
        from src.tasks.qualification_tasks import qualify_restaurant_task

        async_result = qualify_restaurant_task.delay(str(restaurant_id), str(lead.id))
        result.qualification_task_ids.append(async_result.id)

    logger.info("prospect_promoted", restaurant_id=str(restaurant_id), lead_id=str(lead.id))
