"""ICP scoring engine — weighted composite scoring for restaurant leads."""

from datetime import datetime, timezone
from typing import Any

from src.config import settings
from src.scoring.signals import detect_chain, detect_pos, detect_delivery, normalize_review_signal
from src.scoring.geo_density import compute_density_scores
from src.utils.logging import get_logger

logger = get_logger("scoring.icp")


class ICPScorer:
    """Compute ICP (Ideal Customer Profile) scores for restaurants."""

    def __init__(self):
        self.weights = {
            "independent": settings.weight_independent,
            "delivery": settings.weight_delivery,
            "pos": settings.weight_pos,
            "density": settings.weight_density,
            "reviews": settings.weight_reviews,
        }

    def score_restaurant(
        self,
        restaurant: dict[str, Any],
        source_records: list[dict],
        density_score: float = 0.0,
    ) -> dict[str, Any]:
        """Compute ICP score for a single restaurant.

        Args:
            restaurant: Restaurant data dict.
            source_records: All source records for this restaurant.
            density_score: Pre-computed geo density score (0-1).

        Returns:
            Dict with all scoring signals and final score.
        """
        # 1. Independence signal
        is_chain, chain_name = detect_chain(
            restaurant.get("name", ""),
            restaurant.get("extracted_data"),
        )
        is_independent = not is_chain

        # 2. Delivery signal
        has_delivery, delivery_platforms = detect_delivery(source_records)

        # 3. POS signal
        raw_text = ""
        extracted = {}
        for rec in source_records:
            if rec.get("source") == "website":
                raw_text = (rec.get("raw_data") or {}).get("raw_text", "")
            if rec.get("extracted_data"):
                extracted.update(rec["extracted_data"])
        has_pos, pos_provider = detect_pos(raw_text, extracted)

        # 4. Review signal
        review_count = restaurant.get("review_count", 0) or 0
        rating = restaurant.get("rating", 0.0) or 0.0
        review_signal = normalize_review_signal(review_count, rating)

        # Weighted composite score (0-100)
        total_score = (
            self.weights["independent"] * (1.0 if is_independent else 0.0)
            + self.weights["delivery"] * (1.0 if has_delivery else 0.0)
            + self.weights["pos"] * (1.0 if has_pos else 0.0)
            + self.weights["density"] * density_score
            + self.weights["reviews"] * review_signal
        )
        total_score = round(total_score, 2)

        fit_label = self._classify_fit(total_score)

        result = {
            "is_independent": is_independent,
            "is_chain": is_chain,
            "chain_name": chain_name,
            "has_delivery": has_delivery,
            "delivery_platforms": delivery_platforms,
            "has_pos": has_pos,
            "pos_provider": pos_provider,
            "geo_density_score": density_score,
            "review_volume": review_count,
            "rating_avg": rating,
            "review_signal": review_signal,
            "total_icp_score": total_score,
            "fit_label": fit_label,
            "scoring_version": settings.scoring_version,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info("restaurant_scored", name=restaurant.get("name"), score=total_score, fit=fit_label)
        return result

    def _classify_fit(self, score: float) -> str:
        """Classify ICP fit based on score thresholds."""
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
                "batch_scoring_complete",
                count=len(results),
                avg_score=round(sum(scores) / len(scores), 2),
                excellent=sum(1 for r in results if r["fit_label"] == "excellent"),
                good=sum(1 for r in results if r["fit_label"] == "good"),
            )

        return results


icp_scorer = ICPScorer()
