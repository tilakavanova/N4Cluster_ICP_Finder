"""A/B Testing framework for message templates and scoring profiles (NIF-238, NIF-262).

Provides experiment creation, deterministic variant assignment, outcome recording,
and statistical significance testing via two-sample z-test for proportions.
"""

import hashlib
import math
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ABExperiment, ABAssignment, ScoringProfile
from src.utils.logging import get_logger

logger = get_logger("ab_testing")


class ABTestService:
    """A/B testing for message templates and scoring profiles."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ── Experiment lifecycle ─────────────────────────────────────

    async def create_experiment(
        self,
        name: str,
        variants: list[dict],
        metric: str,
        sample_size: int,
        experiment_type: str = "template",
    ) -> ABExperiment:
        """Create a new A/B test experiment.

        Args:
            name: Human-readable experiment name.
            variants: List of variant configs, e.g. [{"name": "A", "template_id": "..."}, ...].
            metric: Target metric — open_rate, click_rate, reply_rate, conversion_rate.
            sample_size: Minimum observations per variant before declaring a winner.
            experiment_type: 'template' or 'scoring_profile'.
        """
        if len(variants) < 2:
            raise ValueError("At least 2 variants are required")
        if metric not in ("open_rate", "click_rate", "reply_rate", "conversion_rate"):
            raise ValueError(f"Invalid metric: {metric}")
        if sample_size < 1:
            raise ValueError("sample_size must be >= 1")

        experiment = ABExperiment(
            name=name,
            experiment_type=experiment_type,
            status="draft",
            variants=variants,
            metric=metric,
            sample_size=sample_size,
        )
        self.session.add(experiment)
        await self.session.flush()
        logger.info(
            "experiment_created",
            experiment_id=str(experiment.id),
            name=name,
            experiment_type=experiment_type,
            variant_count=len(variants),
        )
        return experiment

    async def create_scoring_experiment(
        self,
        name: str,
        profile_a_id: UUID,
        profile_b_id: UUID,
        metric: str = "conversion_rate",
    ) -> ABExperiment:
        """Create an A/B test between two scoring profiles (NIF-262).

        Validates that both profiles exist, then delegates to create_experiment.
        """
        profile_a = await self.session.get(ScoringProfile, profile_a_id)
        if not profile_a:
            raise ValueError(f"Scoring profile {profile_a_id} not found")
        profile_b = await self.session.get(ScoringProfile, profile_b_id)
        if not profile_b:
            raise ValueError(f"Scoring profile {profile_b_id} not found")

        variants = [
            {"name": "A", "scoring_profile_id": str(profile_a_id), "profile_name": profile_a.name},
            {"name": "B", "scoring_profile_id": str(profile_b_id), "profile_name": profile_b.name},
        ]
        return await self.create_experiment(
            name=name,
            variants=variants,
            metric=metric,
            sample_size=100,
            experiment_type="scoring_profile",
        )

    async def start_experiment(self, experiment_id: UUID) -> ABExperiment:
        """Transition experiment from draft to running."""
        experiment = await self.session.get(ABExperiment, experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")
        if experiment.status != "draft":
            raise ValueError(f"Cannot start experiment in '{experiment.status}' status")

        experiment.status = "running"
        experiment.started_at = datetime.now(timezone.utc)
        await self.session.flush()
        logger.info("experiment_started", experiment_id=str(experiment_id))
        return experiment

    # ── Variant assignment ───────────────────────────────────────

    @staticmethod
    def _deterministic_variant(experiment_id: UUID, lead_id: UUID, variant_names: list[str]) -> str:
        """Hash-based deterministic variant assignment.

        Same (experiment, lead) pair always gets the same variant.
        """
        key = f"{experiment_id}:{lead_id}".encode()
        digest = hashlib.sha256(key).hexdigest()
        index = int(digest, 16) % len(variant_names)
        return variant_names[index]

    async def assign_variant(self, experiment_id: UUID, lead_id: UUID) -> ABAssignment:
        """Assign a lead to a variant (idempotent — returns existing if already assigned)."""
        experiment = await self.session.get(ABExperiment, experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")
        if experiment.status != "running":
            raise ValueError(f"Experiment is not running (status={experiment.status})")

        # Check for existing assignment
        result = await self.session.execute(
            select(ABAssignment).where(
                ABAssignment.experiment_id == experiment_id,
                ABAssignment.lead_id == lead_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        variant_names = [v["name"] for v in experiment.variants]
        chosen = self._deterministic_variant(experiment_id, lead_id, variant_names)

        assignment = ABAssignment(
            experiment_id=experiment_id,
            lead_id=lead_id,
            variant_name=chosen,
        )
        self.session.add(assignment)
        await self.session.flush()
        logger.info(
            "variant_assigned",
            experiment_id=str(experiment_id),
            lead_id=str(lead_id),
            variant=chosen,
        )
        return assignment

    # ── Outcome recording ────────────────────────────────────────

    async def record_outcome(
        self,
        experiment_id: UUID,
        lead_id: UUID,
        metric_value: float,
    ) -> ABAssignment:
        """Record the outcome metric for a lead's assignment."""
        result = await self.session.execute(
            select(ABAssignment).where(
                ABAssignment.experiment_id == experiment_id,
                ABAssignment.lead_id == lead_id,
            )
        )
        assignment = result.scalar_one_or_none()
        if not assignment:
            raise ValueError(f"No assignment found for experiment={experiment_id}, lead={lead_id}")

        outcome = assignment.outcome or {}
        outcome["metric_value"] = metric_value
        outcome["recorded_at"] = datetime.now(timezone.utc).isoformat()
        assignment.outcome = outcome
        await self.session.flush()
        return assignment

    # ── Results & statistics ─────────────────────────────────────

    async def get_results(self, experiment_id: UUID) -> dict:
        """Return per-variant stats: count, mean, stddev, confidence interval."""
        experiment = await self.session.get(ABExperiment, experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        result = await self.session.execute(
            select(ABAssignment).where(ABAssignment.experiment_id == experiment_id)
        )
        assignments = result.scalars().all()

        # Group by variant
        variant_data: dict[str, list[float]] = {}
        for a in assignments:
            if a.outcome and "metric_value" in a.outcome:
                variant_data.setdefault(a.variant_name, []).append(a.outcome["metric_value"])

        variant_stats = {}
        for variant_name, values in variant_data.items():
            n = len(values)
            mean = sum(values) / n if n > 0 else 0.0
            variance = sum((v - mean) ** 2 for v in values) / n if n > 1 else 0.0
            stddev = math.sqrt(variance)
            se = stddev / math.sqrt(n) if n > 0 else 0.0
            ci_lower = mean - 1.96 * se
            ci_upper = mean + 1.96 * se

            variant_stats[variant_name] = {
                "count": n,
                "mean": round(mean, 4),
                "stddev": round(stddev, 4),
                "ci_lower": round(ci_lower, 4),
                "ci_upper": round(ci_upper, 4),
            }

        # Count total assignments per variant (including those without outcomes)
        total_assigned: dict[str, int] = {}
        for a in assignments:
            total_assigned[a.variant_name] = total_assigned.get(a.variant_name, 0) + 1

        return {
            "experiment_id": str(experiment_id),
            "name": experiment.name,
            "status": experiment.status,
            "metric": experiment.metric,
            "sample_size": experiment.sample_size,
            "winner_variant": experiment.winner_variant,
            "total_assigned": total_assigned,
            "variant_stats": variant_stats,
        }

    async def declare_winner(self, experiment_id: UUID) -> dict:
        """Statistical significance check. Declare winner if p < 0.05.

        Uses a two-sample z-test for proportions (appropriate for rate metrics).
        Returns the result dict with winner info or reason for no winner.
        """
        experiment = await self.session.get(ABExperiment, experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")
        if experiment.status == "completed":
            return {
                "experiment_id": str(experiment_id),
                "already_completed": True,
                "winner_variant": experiment.winner_variant,
            }

        results = await self.get_results(experiment_id)
        variant_stats = results["variant_stats"]

        if len(variant_stats) < 2:
            return {
                "experiment_id": str(experiment_id),
                "winner": None,
                "reason": "Not enough variants with data",
            }

        # Check minimum sample size
        for vname, stats in variant_stats.items():
            if stats["count"] < experiment.sample_size:
                return {
                    "experiment_id": str(experiment_id),
                    "winner": None,
                    "reason": f"Variant '{vname}' has {stats['count']}/{experiment.sample_size} observations",
                }

        # Two-sample z-test between best two variants
        sorted_variants = sorted(variant_stats.items(), key=lambda x: x[1]["mean"], reverse=True)
        best_name, best_stats = sorted_variants[0]
        second_name, second_stats = sorted_variants[1]

        p_value = self._z_test_proportions(
            best_stats["mean"], best_stats["count"],
            second_stats["mean"], second_stats["count"],
        )

        significant = p_value < 0.05
        if significant:
            experiment.status = "completed"
            experiment.winner_variant = best_name
            experiment.ended_at = datetime.now(timezone.utc)
            await self.session.flush()
            logger.info(
                "winner_declared",
                experiment_id=str(experiment_id),
                winner=best_name,
                p_value=round(p_value, 6),
            )

        return {
            "experiment_id": str(experiment_id),
            "winner": best_name if significant else None,
            "p_value": round(p_value, 6),
            "significant": significant,
            "best_variant": best_name,
            "best_mean": best_stats["mean"],
            "second_variant": second_name,
            "second_mean": second_stats["mean"],
        }

    @staticmethod
    def _z_test_proportions(p1: float, n1: int, p2: float, n2: int) -> float:
        """Two-sample z-test for proportions. Returns p-value.

        Used for rate metrics (open_rate, click_rate, etc.) where values are 0-1.
        """
        if n1 == 0 or n2 == 0:
            return 1.0

        # Pooled proportion
        p_pool = (p1 * n1 + p2 * n2) / (n1 + n2)
        if p_pool <= 0 or p_pool >= 1:
            return 1.0

        se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
        if se == 0:
            return 1.0

        z = abs(p1 - p2) / se

        # Approximate two-tailed p-value using the complementary error function
        p_value = math.erfc(z / math.sqrt(2))
        return p_value

    # ── Scoring profile experiment helpers (NIF-262) ─────────────

    async def get_active_scoring_experiment(self) -> ABExperiment | None:
        """Return the currently running scoring profile experiment, if any."""
        result = await self.session.execute(
            select(ABExperiment).where(
                ABExperiment.experiment_type == "scoring_profile",
                ABExperiment.status == "running",
            ).order_by(ABExperiment.started_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_scoring_profile_for_restaurant(
        self,
        experiment: ABExperiment,
        lead_id: UUID,
    ) -> UUID | None:
        """Given an active scoring experiment and a lead, return the assigned profile UUID.

        This enables the scoring engine to use the correct profile for A/B testing.
        """
        assignment = await self.assign_variant(experiment.id, lead_id)
        # Find the variant config
        for variant in experiment.variants:
            if variant["name"] == assignment.variant_name:
                profile_id_str = variant.get("scoring_profile_id")
                if profile_id_str:
                    return UUID(profile_id_str)
        return None
