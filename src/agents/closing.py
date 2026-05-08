"""ClosingAgent — demo scheduling, follow-ups, sales handoff (NIF-272).

Manages the closing phase of the sales pipeline: scheduling demos,
sequencing follow-up communications, and handing off to sales reps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import BaseAgent, AgentResult, register_agent
from src.utils.logging import get_logger

logger = get_logger("agents.closing")

# Follow-up sequence template (days after initial contact)
DEFAULT_FOLLOWUP_SEQUENCE = [
    {"day": 1, "channel": "email", "action": "thank_you"},
    {"day": 3, "channel": "email", "action": "value_prop"},
    {"day": 5, "channel": "sms", "action": "check_in"},
    {"day": 7, "channel": "call", "action": "demo_offer"},
    {"day": 14, "channel": "email", "action": "case_study"},
]


class ClosingAgent(BaseAgent):
    name = "closing"
    description = "Manages demo scheduling, follow-up sequences, and sales handoff"

    async def run(
        self,
        context: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> AgentResult:
        """Plan closing actions for a lead.

        Expected context keys:
            - lead_id: str — UUID of the lead
            - action: str — one of: schedule_demo, plan_followups, handoff
            - demo_date: str | None — ISO date for demo scheduling
            - rep_id: str | None — sales rep for handoff
        """
        lead_id = context.get("lead_id")
        action = context.get("action", "plan_followups")

        if not lead_id:
            return AgentResult(success=False, errors=["lead_id required"])

        if action == "schedule_demo":
            return await self._schedule_demo(context, session)
        elif action == "plan_followups":
            return self._plan_followups(context)
        elif action == "handoff":
            return await self._handoff(context, session)
        else:
            return AgentResult(success=False, errors=[f"Unknown action: {action}"])

    async def _schedule_demo(
        self,
        context: dict[str, Any],
        session: AsyncSession | None,
    ) -> AgentResult:
        """Schedule a demo for a lead."""
        lead_id = context["lead_id"]
        demo_date = context.get("demo_date")

        if not demo_date:
            # Suggest next Tuesday/Thursday at 2 PM
            now = datetime.now(timezone.utc)
            days_until_tue = (1 - now.weekday()) % 7 or 7
            suggested = now + timedelta(days=days_until_tue)
            suggested = suggested.replace(hour=14, minute=0, second=0, microsecond=0)
            demo_date = suggested.isoformat()

        return AgentResult(
            success=True,
            data={
                "lead_id": lead_id,
                "action": "demo_scheduled",
                "demo_date": demo_date,
                "status": "pending_confirmation",
            },
        )

    def _plan_followups(self, context: dict[str, Any]) -> AgentResult:
        """Generate a follow-up sequence plan."""
        lead_id = context["lead_id"]
        now = datetime.now(timezone.utc)

        sequence = []
        for step in DEFAULT_FOLLOWUP_SEQUENCE:
            scheduled = now + timedelta(days=step["day"])
            sequence.append({
                "day": step["day"],
                "scheduled_at": scheduled.isoformat(),
                "channel": step["channel"],
                "action": step["action"],
            })

        return AgentResult(
            success=True,
            data={
                "lead_id": lead_id,
                "action": "followup_plan",
                "sequence": sequence,
                "total_steps": len(sequence),
            },
        )

    async def _handoff(
        self,
        context: dict[str, Any],
        session: AsyncSession | None,
    ) -> AgentResult:
        """Hand off a lead to a sales rep."""
        lead_id = context["lead_id"]
        rep_id = context.get("rep_id")

        if not rep_id:
            return AgentResult(
                success=False,
                errors=["rep_id required for handoff"],
            )

        # In production, this would update Lead.owner and create a RepQueueItem
        return AgentResult(
            success=True,
            data={
                "lead_id": lead_id,
                "action": "handoff",
                "rep_id": rep_id,
                "status": "handed_off",
            },
        )


# Auto-register
_agent = ClosingAgent()
register_agent(_agent)
