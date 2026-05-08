"""OutreachAgent — channel selection, personalisation, send-time prediction (NIF-271).

Determines the best outreach channel (email, SMS, call), personalises the
message, and predicts optimal send time for each target.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import BaseAgent, AgentResult, register_agent
from src.utils.logging import get_logger

logger = get_logger("agents.outreach")

# Simple heuristics for channel selection (can be replaced with ML model)
CHANNEL_PRIORITY = {
    "email": 0.4,
    "sms": 0.35,
    "call": 0.25,
}

# Send-time windows by day of week (hour ranges, UTC)
OPTIMAL_SEND_WINDOWS = {
    "email": {"start_hour": 9, "end_hour": 11},  # 9-11 AM local
    "sms": {"start_hour": 10, "end_hour": 14},    # 10 AM - 2 PM local
    "call": {"start_hour": 14, "end_hour": 16},   # 2-4 PM local
}


class OutreachAgent(BaseAgent):
    name = "outreach"
    description = "Selects outreach channel, personalises messages, and predicts send time"

    async def run(
        self,
        context: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> AgentResult:
        """Plan outreach for a lead/restaurant.

        Expected context keys:
            - lead_id: str — UUID of the lead
            - restaurant_id: str — UUID of the restaurant
            - preferred_channel: str | None — override channel selection
        """
        lead_id = context.get("lead_id")
        restaurant_id = context.get("restaurant_id")
        preferred_channel = context.get("preferred_channel")

        if not lead_id and not restaurant_id:
            return AgentResult(success=False, errors=["lead_id or restaurant_id required"])

        # Channel selection
        channel = preferred_channel if preferred_channel in CHANNEL_PRIORITY else _select_channel(context)

        # Send-time prediction
        send_window = OPTIMAL_SEND_WINDOWS.get(channel, OPTIMAL_SEND_WINDOWS["email"])

        # Personalisation hints
        personalisation = _build_personalisation(context)

        return AgentResult(
            success=True,
            data={
                "recommended_channel": channel,
                "send_window": send_window,
                "personalisation": personalisation,
                "lead_id": lead_id,
                "restaurant_id": restaurant_id,
            },
            metadata={"channel_scores": CHANNEL_PRIORITY},
        )


def _select_channel(context: dict[str, Any]) -> str:
    """Select the best outreach channel based on available data."""
    # If lead has phone, prefer SMS for restaurants
    has_phone = bool(context.get("phone"))
    has_email = bool(context.get("email"))
    business_type = context.get("business_type", "restaurant")

    if has_phone and business_type == "restaurant":
        return "sms"
    elif has_email:
        return "email"
    else:
        return "call"


def _build_personalisation(context: dict[str, Any]) -> dict[str, Any]:
    """Build personalisation hints for the outreach message."""
    return {
        "first_name": context.get("first_name", ""),
        "company": context.get("company", ""),
        "city": context.get("city", ""),
        "business_type": context.get("business_type", "restaurant"),
        "icp_score": context.get("icp_score"),
        "talking_points": _generate_talking_points(context),
    }


def _generate_talking_points(context: dict[str, Any]) -> list[str]:
    """Generate talking points based on lead context."""
    points = []
    if context.get("has_delivery"):
        points.append("Already on delivery platforms — can optimise existing setup")
    if context.get("is_independent"):
        points.append("Independent restaurant — full control over tech stack")
    if context.get("icp_score") and context["icp_score"] > 70:
        points.append("High-fit prospect — prioritise for personalised outreach")
    if not points:
        points.append("General introduction to our platform benefits")
    return points


# Auto-register
_agent = OutreachAgent()
register_agent(_agent)
