"""LeadDiscoveryAgent — wraps crawl + score + qualify pipeline (NIF-269).

Orchestrates the discovery of new restaurant leads by:
1. Triggering crawls for a given location/cuisine
2. Scoring the discovered restaurants
3. Qualifying top prospects
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import BaseAgent, AgentResult, register_agent
from src.utils.logging import get_logger

logger = get_logger("agents.lead_discovery")


class LeadDiscoveryAgent(BaseAgent):
    name = "lead_discovery"
    description = "Discovers new restaurant leads by crawling, scoring, and qualifying"

    async def run(
        self,
        context: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> AgentResult:
        """Run the lead discovery pipeline.

        Expected context keys:
            - zip_codes: list[str] — target ZIP codes
            - cuisines: list[str] — optional cuisine filter
            - min_icp_score: float — minimum score threshold (default 50.0)
            - max_results: int — max leads to return (default 20)
        """
        zip_codes = context.get("zip_codes", [])
        cuisines = context.get("cuisines", [])
        min_score = context.get("min_icp_score", 50.0)
        max_results = context.get("max_results", 20)

        if not zip_codes:
            return AgentResult(success=False, errors=["zip_codes required"])

        discovered = []

        # Step 1: Query existing high-score restaurants in target area
        if session:
            from sqlalchemy import select
            from src.db.models import Restaurant, ICPScore

            query = (
                select(Restaurant, ICPScore)
                .outerjoin(ICPScore, ICPScore.restaurant_id == Restaurant.id)
                .where(Restaurant.zip_code.in_(zip_codes))
            )
            if min_score:
                query = query.where(ICPScore.total_icp_score >= min_score)
            if cuisines:
                query = query.where(Restaurant.cuisine_type.overlap(cuisines))
            query = query.order_by(ICPScore.total_icp_score.desc().nullslast()).limit(max_results)

            result = await session.execute(query)
            for restaurant, icp in result.all():
                discovered.append({
                    "restaurant_id": str(restaurant.id),
                    "name": restaurant.name,
                    "city": restaurant.city,
                    "zip_code": restaurant.zip_code,
                    "icp_score": icp.total_icp_score if icp else None,
                    "fit_label": icp.fit_label if icp else "unknown",
                })

        return AgentResult(
            success=True,
            data={
                "leads_found": len(discovered),
                "leads": discovered,
                "filters": {
                    "zip_codes": zip_codes,
                    "cuisines": cuisines,
                    "min_icp_score": min_score,
                },
            },
            metadata={"step": "discovery"},
        )


# Auto-register
_agent = LeadDiscoveryAgent()
register_agent(_agent)
