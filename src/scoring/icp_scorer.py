"""ICP scoring engine v2 — 8-signal model aligned to TCT ICP strategy."""

from datetime import datetime, timezone
from typing import Any

from src.config import settings
from src.scoring.signals import (
    detect_chain, detect_delivery, detect_pos,
    platform_dependency_score, pos_maturity_score,
    volume_proxy_score, cuisine_fit_score, price_point_score,
    engagement_recency_score, communication_engagement_score,
    compute_disqualifier_penalty,
)
from src.scoring.geo_density import compute_density_scores
from src.utils.logging import get_logger

logger = get_logger("scoring.icp")


class ICPScorer:
    """Compute ICP scores using 9-signal model with disqualifiers."""

    def __init__(self):
        self.weights = {
            "independent": settings.weight_independent,           # 15%
            "platform_dependency": settings.weight_platform_dependency,  # 20%
            "pos": settings.weight_pos,                           # 12%
            "density": settings.weight_density,                   # 12%
            "volume": settings.weight_volume,                     # 15%
            "cuisine_fit": settings.weight_cuisine_fit,           # 10%
            "price_point": settings.weight_price_point,           # 8%
            "engagement": settings.weight_engagement,             # 8%
            "communication_engagement": settings.weight_communication_engagement,  # 0% default (NIF-236)
        }

    def score_restaurant(
        self,
        restaurant: dict[str, Any],
        source_records: list[dict],
        density_score: float = 0.0,
        tracker_events: list[dict] | None = None,
        outreach_activities: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Compute ICP score for a single restaurant using 9 signals."""

        # Signal 1: Independence (15%)
        is_chain, chain_name = detect_chain(
            restaurant.get("name", ""),
            restaurant.get("extracted_data"),
        )
        is_independent = not is_chain
        independence_signal = 1.0 if is_independent else 0.0

        # Signal 2: Platform Dependency (20%)
        has_delivery, delivery_platforms, platform_count = detect_delivery(source_records)
        platform_dep_signal = platform_dependency_score(platform_count)

        # Signal 3: POS System (12%)
        raw_text = ""
        extracted = {}
        for rec in source_records:
            if rec.get("source") == "website":
                raw_text = (rec.get("raw_data") or {}).get("raw_text", "")
            if rec.get("extracted_data"):
                extracted.update(rec["extracted_data"])
        has_pos, pos_provider = detect_pos(raw_text, extracted)
        pos_signal = pos_maturity_score(has_pos, pos_provider)

        # Signal 4: Geo-Density (12%) — pre-computed
        density_signal = density_score

        # Signal 5: Volume/Revenue Proxy (15%)
        review_count = restaurant.get("review_count", 0) or 0
        rating = restaurant.get("rating", 0.0) or 0.0
        volume_signal = volume_proxy_score(review_count, rating)

        # Signal 6: Cuisine/Category Fit (10%)
        cuisine_types = restaurant.get("cuisine_type", []) or []
        price_tier = restaurant.get("price_tier") or restaurant.get("price")
        cuisine_signal = cuisine_fit_score(cuisine_types, price_tier)

        # Signal 7: Price Point Fit (8%)
        price_signal = price_point_score(price_tier)

        # Signal 8: Engagement/Recency (8%)
        latest_review = restaurant.get("latest_review_date")
        engagement_signal = engagement_recency_score(review_count, rating, latest_review)

        # Signal 9: Communication Engagement (0% default — NIF-236)
        comm_signal = communication_engagement_score(tracker_events, outreach_activities)

        # Build signal map for weighted scoring
        signals = {
            "independent": independence_signal,
            "platform_dependency": platform_dep_signal,
            "pos": pos_signal,
            "density": density_signal,
            "volume": volume_signal,
            "cuisine_fit": cuisine_signal,
            "price_point": price_signal,
            "engagement": engagement_signal,
            "communication_engagement": comm_signal,
        }

        # If communication_engagement is None (no data), redistribute its
        # weight proportionally across the other signals.
        active_weights = dict(self.weights)
        if comm_signal is None:
            excluded_weight = active_weights.pop("communication_engagement", 0.0)
            remaining_total = sum(active_weights.values())
            if remaining_total > 0 and excluded_weight > 0:
                scale = (remaining_total + excluded_weight) / remaining_total
                active_weights = {k: v * scale for k, v in active_weights.items()}

        # Weighted composite score (0-100)
        total_score = sum(
            active_weights.get(k, 0.0) * (v if v is not None else 0.0)
            for k, v in signals.items()
        )

        # Apply disqualifier penalties
        is_fine_dining = cuisine_signal <= 0.2
        penalty = compute_disqualifier_penalty(is_chain, is_fine_dining, has_delivery, review_count)
        total_score = max(0.0, total_score - penalty)
        total_score = round(total_score, 2)

        fit_label = self._classify_fit(total_score)

        result = {
            "is_independent": is_independent,
            "is_chain": is_chain,
            "chain_name": chain_name,
            "has_delivery": has_delivery,
            "delivery_platforms": delivery_platforms,
            "delivery_platform_count": platform_count,
            "has_pos": has_pos,
            "pos_provider": pos_provider,
            "geo_density_score": density_score,
            "review_volume": review_count,
            "rating_avg": rating,
            "volume_proxy": round(volume_signal, 3),
            "cuisine_fit": round(cuisine_signal, 3),
            "price_tier": price_tier,
            "price_point_fit": round(price_signal, 3),
            "engagement_recency": round(engagement_signal, 3),
            "communication_engagement": round(comm_signal, 3) if comm_signal is not None else None,
            "disqualifier_penalty": round(penalty, 2),
            "total_icp_score": total_score,
            "fit_label": fit_label,
            "hot_lead": total_score >= 75,
            "scoring_version": settings.scoring_version,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "restaurant_scored_v2",
            name=restaurant.get("name"),
            score=total_score,
            fit=fit_label,
            penalty=penalty,
            platforms=platform_count,
        )
        return result

    def _classify_fit(self, score: float) -> str:
        if score >= 75:
            return "excellent"
        elif score >= 55:
            return "good"
        elif score >= 35:
            return "moderate"
        else:
            return "poor"

    def score_batch(
        self,
        restaurants: list[dict],
        source_records_map: dict[str, list[dict]],
        density_scores: dict[str, float] | None = None,
    ) -> list[dict]:
        """Score a batch of restaurants."""
        if density_scores is None:
            density_scores = compute_density_scores(restaurants)

        results = []
        for restaurant in restaurants:
            rid = str(restaurant.get("id", ""))
            records = source_records_map.get(rid, [])
            density = density_scores.get(rid, 0.0)

            score = self.score_restaurant(restaurant, records, density)
            score["restaurant_id"] = rid
            results.append(score)

        scores = [r["total_icp_score"] for r in results]
        if scores:
            logger.info(
                "batch_scoring_v2_complete",
                count=len(results),
                avg_score=round(sum(scores) / len(scores), 2),
                excellent=sum(1 for r in results if r["fit_label"] == "excellent"),
                good=sum(1 for r in results if r["fit_label"] == "good"),
            )

        return results


icp_scorer = ICPScorer()
