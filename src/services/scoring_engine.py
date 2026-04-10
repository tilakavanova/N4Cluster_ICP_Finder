"""Configurable Scoring Engine — profile-driven ICP scoring (NIF-125 through NIF-132)."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Restaurant, SourceRecord, ICPScore,
    ScoringProfile, ScoringRule, ScoreExplanation,
    ScoreVersion, ScoreRecalcJob,
)
from src.scoring.signals import (
    detect_chain, detect_delivery, detect_pos,
    platform_dependency_score, pos_maturity_score,
    volume_proxy_score, cuisine_fit_score, price_point_score,
    engagement_recency_score, compute_disqualifier_penalty,
)
from src.utils.logging import get_logger

logger = get_logger("scoring_engine")

# Built-in signal evaluators keyed by signal name
SIGNAL_EVALUATORS = {
    "independent": "_eval_independent",
    "platform_dependency": "_eval_platform_dependency",
    "pos": "_eval_pos",
    "density": "_eval_density",
    "volume": "_eval_volume",
    "cuisine_fit": "_eval_cuisine_fit",
    "price_point": "_eval_price_point",
    "engagement": "_eval_engagement",
}


def _classify_fit(score: float) -> str:
    if score >= 75:
        return "excellent"
    elif score >= 55:
        return "good"
    elif score >= 35:
        return "moderate"
    return "poor"


def _build_restaurant_context(restaurant: Restaurant, source_records: list[SourceRecord]) -> dict:
    """Collect data needed by signal evaluators from ORM objects."""
    records_dicts = []
    raw_text = ""
    extracted = {}
    for sr in source_records:
        rec = {
            "source": sr.source,
            "raw_data": sr.raw_data or {},
            "extracted_data": sr.extracted_data or {},
        }
        records_dicts.append(rec)
        if sr.source == "website":
            raw_text = (sr.raw_data or {}).get("raw_text", "")
        if sr.extracted_data:
            extracted.update(sr.extracted_data)

    return {
        "restaurant": restaurant,
        "source_records": records_dicts,
        "raw_text": raw_text,
        "extracted": extracted,
    }


def _eval_independent(ctx: dict) -> tuple[float, str]:
    r = ctx["restaurant"]
    is_chain, chain_name = detect_chain(r.name or "", None)
    val = 0.0 if is_chain else 1.0
    explanation = f"{'Chain' if is_chain else 'Independent'} restaurant"
    if is_chain and chain_name:
        explanation += f" ({chain_name})"
    return val, explanation


def _eval_platform_dependency(ctx: dict) -> tuple[float, str]:
    has_del, platforms, count = detect_delivery(ctx["source_records"])
    val = platform_dependency_score(count)
    explanation = f"{count} delivery platform(s): {', '.join(platforms) if platforms else 'none'}"
    return val, explanation


def _eval_pos(ctx: dict) -> tuple[float, str]:
    has_pos, provider = detect_pos(ctx["raw_text"], ctx["extracted"])
    val = pos_maturity_score(has_pos, provider)
    explanation = f"POS: {provider}" if has_pos else "No POS detected"
    return val, explanation


def _eval_density(ctx: dict) -> tuple[float, str]:
    r = ctx["restaurant"]
    # Use pre-computed density from ICPScore if available
    val = 0.0
    if hasattr(r, "icp_score") and r.icp_score:
        val = r.icp_score.geo_density_score or 0.0
    return val, f"Geo-density score: {val:.2f}"


def _eval_volume(ctx: dict) -> tuple[float, str]:
    r = ctx["restaurant"]
    review_count = r.review_count or 0
    rating = r.rating_avg or 0.0
    val = volume_proxy_score(review_count, rating)
    return val, f"{review_count} reviews, {rating:.1f} avg rating"


def _eval_cuisine_fit(ctx: dict) -> tuple[float, str]:
    r = ctx["restaurant"]
    val = cuisine_fit_score(r.cuisine_type or [], r.price_tier)
    return val, f"Cuisine: {', '.join(r.cuisine_type or ['unknown'])}"


def _eval_price_point(ctx: dict) -> tuple[float, str]:
    r = ctx["restaurant"]
    val = price_point_score(r.price_tier)
    return val, f"Price tier: {r.price_tier or 'unknown'}"


def _eval_engagement(ctx: dict) -> tuple[float, str]:
    r = ctx["restaurant"]
    val = engagement_recency_score(r.review_count or 0, r.rating_avg or 0.0)
    return val, f"Engagement score based on {r.review_count or 0} reviews"


_EVAL_MAP = {
    "independent": _eval_independent,
    "platform_dependency": _eval_platform_dependency,
    "pos": _eval_pos,
    "density": _eval_density,
    "volume": _eval_volume,
    "cuisine_fit": _eval_cuisine_fit,
    "price_point": _eval_price_point,
    "engagement": _eval_engagement,
}


def _apply_rules(rules: list[ScoringRule], signal_name: str, raw_value: float) -> float:
    """Apply scoring rules to adjust signal points."""
    bonus = 0.0
    for rule in rules:
        if rule.signal_name != signal_name:
            continue
        cond = rule.condition or {}
        if rule.rule_type == "threshold":
            if raw_value >= cond.get("min", 0):
                bonus += rule.points
        elif rule.rule_type == "range":
            if cond.get("min", 0) <= raw_value <= cond.get("max", 1):
                bonus += rule.points
        elif rule.rule_type == "boolean":
            if (raw_value > 0.5) == cond.get("expected", True):
                bonus += rule.points
        elif rule.rule_type == "custom":
            # Custom rules just add flat points
            bonus += rule.points
    return bonus


async def evaluate_restaurant(
    session: AsyncSession,
    restaurant_id: UUID,
    profile_id: UUID,
) -> ScoreExplanation:
    """Score a restaurant using a configurable profile (NIF-125-128)."""
    profile = await session.get(ScoringProfile, profile_id)
    if not profile:
        raise ValueError(f"Scoring profile {profile_id} not found")

    restaurant = await session.get(Restaurant, restaurant_id)
    if not restaurant:
        raise ValueError(f"Restaurant {restaurant_id} not found")

    # Load source records
    result = await session.execute(
        select(SourceRecord).where(SourceRecord.restaurant_id == restaurant_id)
    )
    source_records = result.scalars().all()

    # Load rules
    rules_result = await session.execute(
        select(ScoringRule).where(ScoringRule.profile_id == profile_id)
    )
    rules = rules_result.scalars().all()

    ctx = _build_restaurant_context(restaurant, source_records)
    signals_config = profile.signals or []

    breakdown = []
    total_score = 0.0

    for sig_cfg in signals_config:
        sig_name = sig_cfg.get("name")
        weight = sig_cfg.get("weight", 0.0)
        enabled = sig_cfg.get("enabled", True)
        if not enabled or sig_name not in _EVAL_MAP:
            continue

        evaluator = _EVAL_MAP[sig_name]
        raw_value, explanation = evaluator(ctx)

        # Apply rules bonus/penalty
        rule_bonus = _apply_rules(rules, sig_name, raw_value)

        weighted_value = weight * raw_value + rule_bonus
        total_score += weighted_value

        breakdown.append({
            "signal": sig_name,
            "raw_value": round(raw_value, 3),
            "weight": weight,
            "weighted_value": round(weighted_value, 3),
            "rule_bonus": round(rule_bonus, 2),
            "explanation": explanation,
        })

    total_score = round(max(0.0, min(total_score, 100.0)), 2)
    fit_label = _classify_fit(total_score)

    explanation_text = "; ".join(
        f"{b['signal']}: {b['raw_value']:.2f} x {b['weight']} = {b['weighted_value']:.2f}"
        for b in breakdown
    )

    # Upsert explanation
    existing = await session.execute(
        select(ScoreExplanation).where(
            ScoreExplanation.restaurant_id == restaurant_id,
            ScoreExplanation.profile_id == profile_id,
        )
    )
    score_exp = existing.scalar_one_or_none()
    if score_exp:
        score_exp.signal_breakdown = breakdown
        score_exp.total_score = total_score
        score_exp.fit_label = fit_label
        score_exp.explanation_text = explanation_text
        score_exp.scored_at = datetime.now(timezone.utc)
    else:
        score_exp = ScoreExplanation(
            restaurant_id=restaurant_id,
            profile_id=profile_id,
            signal_breakdown=breakdown,
            total_score=total_score,
            fit_label=fit_label,
            explanation_text=explanation_text,
        )
        session.add(score_exp)

    await session.flush()
    logger.info("restaurant_scored", restaurant=str(restaurant_id), profile=profile.name, score=total_score, fit=fit_label)
    return score_exp


async def explain_score(
    session: AsyncSession,
    restaurant_id: UUID,
    profile_id: UUID,
) -> dict | None:
    """Return the stored score explanation for a restaurant+profile (NIF-127)."""
    result = await session.execute(
        select(ScoreExplanation).where(
            ScoreExplanation.restaurant_id == restaurant_id,
            ScoreExplanation.profile_id == profile_id,
        )
    )
    exp = result.scalar_one_or_none()
    if not exp:
        return None
    return {
        "restaurant_id": str(exp.restaurant_id),
        "profile_id": str(exp.profile_id),
        "total_score": exp.total_score,
        "fit_label": exp.fit_label,
        "signal_breakdown": exp.signal_breakdown,
        "explanation_text": exp.explanation_text,
        "scored_at": exp.scored_at.isoformat() if exp.scored_at else None,
    }


async def recalculate_batch(
    session: AsyncSession,
    profile_id: UUID,
) -> ScoreRecalcJob:
    """Create and execute a batch recalculation job (NIF-132)."""
    profile = await session.get(ScoringProfile, profile_id)
    if not profile:
        raise ValueError(f"Scoring profile {profile_id} not found")

    # Count restaurants
    count_result = await session.execute(select(func.count(Restaurant.id)))
    total = count_result.scalar() or 0

    job = ScoreRecalcJob(
        profile_id=profile_id,
        status="running",
        total_items=total,
        processed_items=0,
        started_at=datetime.now(timezone.utc),
    )
    session.add(job)
    await session.flush()

    # Process all restaurants
    result = await session.execute(select(Restaurant.id))
    restaurant_ids = [r[0] for r in result.all()]

    processed = 0
    try:
        for rid in restaurant_ids:
            await evaluate_restaurant(session, rid, profile_id)
            processed += 1
            job.processed_items = processed

        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)[:500]
        job.finished_at = datetime.now(timezone.utc)
        logger.error("recalc_failed", profile=str(profile_id), error=str(exc))

    await session.flush()
    logger.info("recalc_complete", profile=str(profile_id), status=job.status, processed=processed, total=total)
    return job


async def create_version_snapshot(
    session: AsyncSession,
    profile: ScoringProfile,
    changes: dict,
    created_by: str = "system",
) -> ScoreVersion:
    """Record a version snapshot when a profile is updated (NIF-129)."""
    sv = ScoreVersion(
        profile_id=profile.id,
        version_number=profile.version,
        changes=changes,
        created_by=created_by,
    )
    session.add(sv)
    await session.flush()
    return sv
