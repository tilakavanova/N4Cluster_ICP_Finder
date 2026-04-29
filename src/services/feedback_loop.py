"""Conversion feedback loop with auto-adjusting scoring weights (NIF-260).

Analyzes conversion data to identify which ICP score ranges convert best,
then suggests and applies weight adjustments to scoring profiles.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func, and_, case, extract
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    ConversionEvent, Restaurant, ICPScore,
    ScoringProfile, ScoreExplanation, ScoreVersion,
)
from src.services.scoring_engine import create_version_snapshot
from src.utils.logging import get_logger

logger = get_logger("feedback_loop")

# Score buckets for analysis
SCORE_BUCKETS = [
    ("0-25", 0.0, 25.0),
    ("25-50", 25.0, 50.0),
    ("50-75", 50.0, 75.0),
    ("75-100", 75.0, 100.0),
]


def _parse_period_filter(period: str) -> list:
    """Build SQLAlchemy filter clauses for a period string."""
    filters = []
    if "-W" in period:
        parts = period.split("-W")
        year = int(parts[0])
        week = int(parts[1])
        filters.append(extract("isoyear", ConversionEvent.occurred_at) == year)
        filters.append(extract("week", ConversionEvent.occurred_at) == week)
    else:
        parts = period.split("-")
        year = int(parts[0])
        month = int(parts[1])
        filters.append(extract("year", ConversionEvent.occurred_at) == year)
        filters.append(extract("month", ConversionEvent.occurred_at) == month)
    return filters


async def analyze_conversions(
    session: AsyncSession,
    period: str,
) -> dict:
    """Analyze which ICP score ranges have highest conversion rates.

    Groups restaurants by ICP score bucket, counts discovered vs converted
    in the given period, and returns conversion rates per bucket.
    """
    period_filters = _parse_period_filter(period)

    buckets = []
    for label, low, high in SCORE_BUCKETS:
        # Restaurants in this score range
        score_filter = and_(
            ICPScore.total_icp_score >= low,
            ICPScore.total_icp_score < high if high < 100.0 else ICPScore.total_icp_score <= high,
        )

        # Count restaurants in this bucket that had a 'discovered' event
        discovered_q = select(func.count(func.distinct(ConversionEvent.restaurant_id))).where(
            and_(
                *period_filters,
                ConversionEvent.event_type == "discovered",
                ConversionEvent.restaurant_id.in_(
                    select(ICPScore.restaurant_id).where(score_filter)
                ),
            )
        )
        discovered_result = await session.execute(discovered_q)
        discovered = discovered_result.scalar() or 0

        # Count restaurants in this bucket that had a 'converted' event
        converted_q = select(func.count(func.distinct(ConversionEvent.restaurant_id))).where(
            and_(
                *period_filters,
                ConversionEvent.event_type == "converted",
                ConversionEvent.restaurant_id.in_(
                    select(ICPScore.restaurant_id).where(score_filter)
                ),
            )
        )
        converted_result = await session.execute(converted_q)
        converted = converted_result.scalar() or 0

        rate = round((converted / discovered) * 100.0, 2) if discovered > 0 else 0.0

        buckets.append({
            "score_range": label,
            "discovered": discovered,
            "converted": converted,
            "conversion_rate": rate,
        })

    logger.info("conversion_analysis_complete", period=period)
    return {
        "period": period,
        "buckets": buckets,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


async def suggest_weight_adjustments(
    session: AsyncSession,
    period: str,
    profile_id: UUID | None = None,
) -> dict:
    """Compare signal breakdowns of converted vs non-converted restaurants.

    For each signal, compute the average raw_value among converted restaurants
    vs non-converted (discovered but not converted). Signals where converted
    restaurants score significantly higher suggest increasing the weight;
    signals where they score lower suggest decreasing.
    """
    period_filters = _parse_period_filter(period)

    # Get restaurant IDs that converted in this period
    converted_q = select(func.distinct(ConversionEvent.restaurant_id)).where(
        and_(*period_filters, ConversionEvent.event_type == "converted")
    )
    converted_result = await session.execute(converted_q)
    converted_ids = {r[0] for r in converted_result.all()}

    # Get restaurant IDs that were discovered but not converted
    discovered_q = select(func.distinct(ConversionEvent.restaurant_id)).where(
        and_(*period_filters, ConversionEvent.event_type == "discovered")
    )
    discovered_result = await session.execute(discovered_q)
    discovered_ids = {r[0] for r in discovered_result.all()}
    not_converted_ids = discovered_ids - converted_ids

    if not converted_ids:
        return {
            "period": period,
            "adjustments": [],
            "message": "No conversions found in this period",
        }

    # Find a profile to analyze against
    if profile_id:
        profile = await session.get(ScoringProfile, profile_id)
    else:
        result = await session.execute(
            select(ScoringProfile).where(ScoringProfile.is_active == True).limit(1)
        )
        profile = result.scalar_one_or_none()

    if not profile:
        return {
            "period": period,
            "adjustments": [],
            "message": "No scoring profile found",
        }

    # Get score explanations for converted restaurants
    conv_expl_q = select(ScoreExplanation).where(
        and_(
            ScoreExplanation.profile_id == profile.id,
            ScoreExplanation.restaurant_id.in_(converted_ids),
        )
    )
    conv_expl_result = await session.execute(conv_expl_q)
    conv_explanations = conv_expl_result.scalars().all()

    # Get score explanations for non-converted restaurants
    nonconv_expl_q = select(ScoreExplanation).where(
        and_(
            ScoreExplanation.profile_id == profile.id,
            ScoreExplanation.restaurant_id.in_(not_converted_ids),
        )
    )
    nonconv_expl_result = await session.execute(nonconv_expl_q)
    nonconv_explanations = nonconv_expl_result.scalars().all()

    # Compute average raw_value per signal for each group
    def _avg_signals(explanations: list) -> dict[str, float]:
        totals: dict[str, list[float]] = {}
        for exp in explanations:
            for item in (exp.signal_breakdown or []):
                sig = item.get("signal", "")
                raw = item.get("raw_value", 0.0)
                totals.setdefault(sig, []).append(raw)
        return {sig: sum(vals) / len(vals) for sig, vals in totals.items() if vals}

    conv_avgs = _avg_signals(conv_explanations)
    nonconv_avgs = _avg_signals(nonconv_explanations)

    # Build adjustments: compare converted avg vs non-converted avg
    all_signals = set(conv_avgs.keys()) | set(nonconv_avgs.keys())
    adjustments = []
    for sig in sorted(all_signals):
        c_avg = conv_avgs.get(sig, 0.0)
        nc_avg = nonconv_avgs.get(sig, 0.0)
        delta = round(c_avg - nc_avg, 4)

        # Find current weight from profile
        current_weight = 0.0
        for s_cfg in (profile.signals or []):
            if s_cfg.get("name") == sig:
                current_weight = s_cfg.get("weight", 0.0)
                break

        # Suggest weight change proportional to delta
        # If converted restaurants score higher on this signal, increase weight
        if abs(delta) > 0.05:
            # Scale adjustment: up to +/- 3 points per signal
            adjustment = round(min(max(delta * 5.0, -3.0), 3.0), 2)
        else:
            adjustment = 0.0

        adjustments.append({
            "signal": sig,
            "current_weight": current_weight,
            "converted_avg": round(c_avg, 4),
            "non_converted_avg": round(nc_avg, 4),
            "delta": delta,
            "suggested_adjustment": adjustment,
            "suggested_new_weight": round(current_weight + adjustment, 2),
        })

    logger.info(
        "weight_adjustments_suggested",
        period=period,
        profile=profile.name,
        num_adjustments=len([a for a in adjustments if a["suggested_adjustment"] != 0]),
    )

    return {
        "period": period,
        "profile_id": str(profile.id),
        "profile_name": profile.name,
        "converted_count": len(converted_ids),
        "not_converted_count": len(not_converted_ids),
        "adjustments": adjustments,
        "suggested_at": datetime.now(timezone.utc).isoformat(),
    }


async def apply_adjustments(
    session: AsyncSession,
    profile_id: UUID,
    adjustments: list[dict],
    approved_by: str = "system",
) -> dict:
    """Apply suggested weight adjustments to a scoring profile.

    Creates a version snapshot before applying changes.

    Args:
        profile_id: The scoring profile to update.
        adjustments: List of dicts with 'signal' and 'new_weight' keys.
        approved_by: User who approved the changes.

    Returns:
        Dict with profile_id, new_version, and applied changes.
    """
    profile = await session.get(ScoringProfile, profile_id)
    if not profile:
        return {"error": "profile_not_found"}

    signals = list(profile.signals or [])
    changes = {}

    for adj in adjustments:
        sig_name = adj.get("signal")
        new_weight = adj.get("new_weight")
        if sig_name is None or new_weight is None:
            continue

        for i, s_cfg in enumerate(signals):
            if s_cfg.get("name") == sig_name:
                old_weight = s_cfg.get("weight", 0.0)
                signals[i] = {**s_cfg, "weight": round(new_weight, 2)}
                changes[sig_name] = {
                    "old_weight": old_weight,
                    "new_weight": round(new_weight, 2),
                }
                break

    if not changes:
        return {
            "profile_id": str(profile_id),
            "message": "No changes applied",
            "changes": {},
        }

    # Create version snapshot before applying
    old_version = profile.version
    profile.version = old_version + 1
    profile.signals = signals
    profile.updated_at = datetime.now(timezone.utc)

    await create_version_snapshot(
        session,
        profile,
        changes={"weight_adjustments": changes, "approved_by": approved_by},
        created_by=approved_by,
    )

    await session.flush()

    logger.info(
        "weight_adjustments_applied",
        profile_id=str(profile_id),
        profile_name=profile.name,
        new_version=profile.version,
        approved_by=approved_by,
        signals_changed=len(changes),
    )

    return {
        "profile_id": str(profile_id),
        "profile_name": profile.name,
        "old_version": old_version,
        "new_version": profile.version,
        "changes": changes,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": approved_by,
    }


async def get_feedback_report(
    session: AsyncSession,
    period: str,
    profile_id: UUID | None = None,
) -> dict:
    """Returns conversion-to-score correlation report.

    Combines conversion analysis with signal-level insights.
    """
    analysis = await analyze_conversions(session, period)
    suggestions = await suggest_weight_adjustments(session, period, profile_id)

    # Compute overall conversion rate
    total_discovered = sum(b["discovered"] for b in analysis["buckets"])
    total_converted = sum(b["converted"] for b in analysis["buckets"])
    overall_rate = round((total_converted / total_discovered) * 100.0, 2) if total_discovered > 0 else 0.0

    return {
        "period": period,
        "overall_conversion_rate": overall_rate,
        "total_discovered": total_discovered,
        "total_converted": total_converted,
        "score_bucket_analysis": analysis["buckets"],
        "signal_analysis": suggestions.get("adjustments", []),
        "profile_id": suggestions.get("profile_id"),
        "profile_name": suggestions.get("profile_name"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
