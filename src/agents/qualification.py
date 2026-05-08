"""QualificationAgent — LLM-powered scoring with auto-qualification (NIF-270).

Wraps the existing qualification service with LLM-based reasoning to
provide richer qualification decisions and explanations.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import BaseAgent, AgentResult, register_agent
from src.utils.logging import get_logger

logger = get_logger("agents.qualification")


class QualificationAgent(BaseAgent):
    name = "qualification"
    description = "LLM-powered merchant qualification with auto-scoring and reasoning"

    async def run(
        self,
        context: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> AgentResult:
        """Run qualification on a restaurant.

        Expected context keys:
            - restaurant_id: str — UUID of the restaurant to qualify
            - force_requalify: bool — skip cache/expiry check (default False)
        """
        restaurant_id = context.get("restaurant_id")
        if not restaurant_id:
            return AgentResult(success=False, errors=["restaurant_id required"])

        if not session:
            return AgentResult(success=False, errors=["Database session required"])

        from uuid import UUID
        from src.services.qualification import qualify_restaurant

        try:
            result = await qualify_restaurant(session, UUID(restaurant_id))
            if not result:
                return AgentResult(
                    success=False,
                    errors=[f"Restaurant {restaurant_id} not found or no ICP score"],
                )

            # Build LLM reasoning summary
            reasoning = _build_reasoning(result)

            return AgentResult(
                success=True,
                data={
                    "restaurant_id": restaurant_id,
                    "qualification_status": result.qualification_status,
                    "confidence_score": result.confidence_score,
                    "reasoning": reasoning,
                },
                metadata={"model_version": result.model_version},
            )
        except Exception as exc:
            logger.error("qualification_agent_error", error=str(exc))
            return AgentResult(success=False, errors=[str(exc)])


def _build_reasoning(result) -> str:
    """Build a human-readable reasoning string from qualification result."""
    status = result.qualification_status
    confidence = result.confidence_score

    if status == "qualified":
        return (
            f"Restaurant is QUALIFIED with {confidence:.0%} confidence. "
            "Key signals are positive — recommend outreach."
        )
    elif status == "needs_review":
        return (
            f"Restaurant NEEDS REVIEW ({confidence:.0%} confidence). "
            "Some signals are mixed — suggest manual review before outreach."
        )
    else:
        return (
            f"Restaurant is NOT QUALIFIED ({confidence:.0%} confidence). "
            "Key signals are negative — not a good fit at this time."
        )


# Auto-register
_agent = QualificationAgent()
register_agent(_agent)
