"""AI Merchant Qualification Agent service (NIF-142 through NIF-144)."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import (
    Restaurant, ICPScore,
    QualificationResult, QualificationExplanation,
)
from src.utils.logging import get_logger

logger = get_logger("qualification")

MODEL_VERSION = "v1"

# Thresholds for qualification decision
QUALIFIED_THRESHOLD = 0.70
NEEDS_REVIEW_THRESHOLD = 0.45
QUALIFICATION_EXPIRY_DAYS = 90

# Signal weights (must sum to 1.0)
SIGNAL_WEIGHTS = {
    "icp_score": 0.35,
    "delivery_presence": 0.20,
    "independence": 0.25,
    "review_volume": 0.20,
}


def _evaluate_icp_score(icp: ICPScore | None) -> tuple[float, str, str]:
    """Evaluate ICP total score signal. Returns (value, impact, explanation)."""
    if not icp or icp.total_icp_score is None:
        return 0.0, "negative", "No ICP score available"
    score = icp.total_icp_score
    normalized = min(score / 100.0, 1.0)
    if normalized >= 0.7:
        impact = "positive"
    elif normalized >= 0.4:
        impact = "neutral"
    else:
        impact = "negative"
    return normalized, impact, f"ICP score {score:.1f}/100 (normalized: {normalized:.2f})"


def _evaluate_delivery(icp: ICPScore | None) -> tuple[float, str, str]:
    """Evaluate delivery platform presence."""
    if not icp:
        return 0.0, "negative", "No delivery data available"
    has_delivery = icp.has_delivery or False
    platform_count = icp.delivery_platform_count or 0
    if not has_delivery:
        return 0.0, "negative", "No delivery platforms detected"
    # More platforms = more established = better prospect
    value = min(platform_count / 3.0, 1.0)
    if platform_count >= 2:
        impact = "positive"
    else:
        impact = "neutral"
    platforms = icp.delivery_platforms or []
    return value, impact, f"{platform_count} delivery platform(s): {', '.join(platforms) if platforms else 'unknown'}"


def _evaluate_independence(icp: ICPScore | None, restaurant: Restaurant) -> tuple[float, str, str]:
    """Evaluate whether restaurant is independent (not a chain)."""
    is_chain = restaurant.is_chain or False
    if icp and icp.is_independent is not None:
        is_independent = icp.is_independent
    else:
        is_independent = not is_chain
    if is_independent:
        return 1.0, "positive", "Independent restaurant — ideal target"
    chain_name = restaurant.chain_name or "unknown chain"
    return 0.0, "negative", f"Chain restaurant ({chain_name}) — not ideal target"


def _evaluate_review_volume(icp: ICPScore | None, restaurant: Restaurant) -> tuple[float, str, str]:
    """Evaluate review volume as a proxy for business activity."""
    review_count = (icp.review_volume if icp else None) or restaurant.review_count or 0
    if review_count >= 200:
        value = 1.0
        impact = "positive"
    elif review_count >= 50:
        value = review_count / 200.0
        impact = "neutral"
    elif review_count > 0:
        value = review_count / 200.0
        impact = "neutral"
    else:
        value = 0.0
        impact = "negative"
    return value, impact, f"{review_count} reviews (activity proxy: {value:.2f})"


def _compute_qualification(
    restaurant: Restaurant,
    icp: ICPScore | None,
) -> tuple[str, float, list[dict], list[dict]]:
    """Run all qualification signals and return status, confidence, signals, explanations."""
    evaluators = {
        "icp_score": lambda: _evaluate_icp_score(icp),
        "delivery_presence": lambda: _evaluate_delivery(icp),
        "independence": lambda: _evaluate_independence(icp, restaurant),
        "review_volume": lambda: _evaluate_review_volume(icp, restaurant),
    }

    signals = []
    explanations_data = []
    weighted_sum = 0.0

    for signal_name, evaluator in evaluators.items():
        value, impact, explanation = evaluator()
        weight = SIGNAL_WEIGHTS[signal_name]
        weighted_sum += value * weight

        signals.append({
            "signal": signal_name,
            "value": round(value, 3),
            "weight": weight,
            "impact": impact,
            "explanation": explanation,
        })

        explanations_data.append({
            "factor_name": signal_name,
            "factor_value": str(round(value, 3)),
            "impact": impact,
            "weight": weight,
            "explanation_text": explanation,
        })

    confidence = round(min(max(weighted_sum, 0.0), 1.0), 4)

    if confidence >= QUALIFIED_THRESHOLD:
        status = "qualified"
    elif confidence >= NEEDS_REVIEW_THRESHOLD:
        status = "needs_review"
    else:
        status = "not_qualified"

    return status, confidence, signals, explanations_data


async def qualify_restaurant(
    session: AsyncSession,
    restaurant_id: UUID,
) -> QualificationResult:
    """Evaluate and qualify a single restaurant. Returns the QualificationResult."""
    restaurant = await session.get(Restaurant, restaurant_id)
    if not restaurant:
        raise ValueError(f"Restaurant {restaurant_id} not found")

    # Load ICP score
    result = await session.execute(
        select(ICPScore).where(ICPScore.restaurant_id == restaurant_id)
    )
    icp = result.scalar_one_or_none()

    status, confidence, signals, explanations_data = _compute_qualification(restaurant, icp)

    now = datetime.now(timezone.utc)
    qualified_at = now if status == "qualified" else None
    expires_at = (now + timedelta(days=QUALIFICATION_EXPIRY_DAYS)) if status == "qualified" else None

    qual_result = QualificationResult(
        restaurant_id=restaurant_id,
        qualification_status=status,
        confidence_score=confidence,
        signals_summary=signals,
        qualified_at=qualified_at,
        expires_at=expires_at,
        model_version=MODEL_VERSION,
    )
    session.add(qual_result)
    await session.flush()

    # Create explanation rows
    for exp_data in explanations_data:
        explanation = QualificationExplanation(
            result_id=qual_result.id,
            factor_name=exp_data["factor_name"],
            factor_value=exp_data["factor_value"],
            impact=exp_data["impact"],
            weight=exp_data["weight"],
            explanation_text=exp_data["explanation_text"],
        )
        session.add(explanation)

    await session.flush()
    logger.info(
        "restaurant_qualified",
        restaurant=str(restaurant_id),
        status=status,
        confidence=confidence,
    )
    return qual_result


async def get_latest_qualification(
    session: AsyncSession,
    restaurant_id: UUID,
) -> QualificationResult | None:
    """Get the most recent qualification result for a restaurant, with explanations."""
    result = await session.execute(
        select(QualificationResult)
        .where(QualificationResult.restaurant_id == restaurant_id)
        .options(selectinload(QualificationResult.explanations))
        .order_by(QualificationResult.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def review_qualification(
    session: AsyncSession,
    result_id: UUID,
    decision: str,
    reviewed_by: str,
    notes: str | None = None,
) -> QualificationResult:
    """Human review override for a qualification result."""
    qual_result = await session.get(QualificationResult, result_id)
    if not qual_result:
        raise ValueError(f"Qualification result {result_id} not found")

    if decision not in ("approved", "rejected"):
        raise ValueError(f"Invalid decision: {decision}. Must be 'approved' or 'rejected'")

    now = datetime.now(timezone.utc)
    qual_result.review_decision = decision
    qual_result.reviewed_by = reviewed_by
    qual_result.reviewed_at = now
    qual_result.review_notes = notes

    # Update status based on review
    if decision == "approved":
        qual_result.qualification_status = "qualified"
        if not qual_result.qualified_at:
            qual_result.qualified_at = now
            qual_result.expires_at = now + timedelta(days=QUALIFICATION_EXPIRY_DAYS)
    else:
        qual_result.qualification_status = "not_qualified"

    await session.flush()
    logger.info(
        "qualification_reviewed",
        result=str(result_id),
        decision=decision,
        reviewed_by=reviewed_by,
    )
    return qual_result


async def batch_qualify(
    session: AsyncSession,
    filters: dict | None = None,
) -> dict:
    """Qualify multiple restaurants based on optional filters."""
    query = select(Restaurant.id)

    if filters:
        if filters.get("city"):
            query = query.where(Restaurant.city == filters["city"])
        if filters.get("state"):
            query = query.where(Restaurant.state == filters["state"])
        if filters.get("zip_code"):
            query = query.where(Restaurant.zip_code == filters["zip_code"])
        if filters.get("min_rating") is not None:
            query = query.where(Restaurant.rating_avg >= filters["min_rating"])
        if filters.get("is_chain") is not None:
            query = query.where(Restaurant.is_chain == filters["is_chain"])

    result = await session.execute(query)
    restaurant_ids = [r[0] for r in result.all()]

    total = len(restaurant_ids)
    qualified_count = 0
    not_qualified_count = 0
    needs_review_count = 0
    errors = []

    for rid in restaurant_ids:
        try:
            qr = await qualify_restaurant(session, rid)
            if qr.qualification_status == "qualified":
                qualified_count += 1
            elif qr.qualification_status == "not_qualified":
                not_qualified_count += 1
            else:
                needs_review_count += 1
        except Exception as exc:
            errors.append({"restaurant_id": str(rid), "error": str(exc)[:200]})
            logger.error("batch_qualify_error", restaurant=str(rid), error=str(exc))

    await session.flush()
    logger.info(
        "batch_qualify_complete",
        total=total,
        qualified=qualified_count,
        not_qualified=not_qualified_count,
        needs_review=needs_review_count,
        errors=len(errors),
    )
    return {
        "total": total,
        "qualified": qualified_count,
        "not_qualified": not_qualified_count,
        "needs_review": needs_review_count,
        "errors": errors,
    }


async def list_pending_review(
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> list[QualificationResult]:
    """List qualification results that need human review."""
    result = await session.execute(
        select(QualificationResult)
        .where(QualificationResult.qualification_status == "needs_review")
        .options(
            selectinload(QualificationResult.explanations),
            selectinload(QualificationResult.restaurant),
        )
        .order_by(QualificationResult.confidence_score.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
