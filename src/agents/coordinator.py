"""CoordinatorAgent — lifecycle handoffs, pipeline health, anomaly escalation (NIF-273).

Orchestrates the full agent pipeline: discovery -> qualification -> outreach -> closing.
Monitors pipeline health and escalates anomalies.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import BaseAgent, AgentResult, get_agent, list_agents, register_agent
from src.utils.logging import get_logger

logger = get_logger("agents.coordinator")

# Agent pipeline order
PIPELINE_STAGES = [
    "lead_discovery",
    "qualification",
    "outreach",
    "closing",
]


class CoordinatorAgent(BaseAgent):
    name = "coordinator"
    description = "Orchestrates agent pipeline, lifecycle handoffs, and anomaly escalation"

    async def run(
        self,
        context: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> AgentResult:
        """Run coordinator logic.

        Expected context keys:
            - action: str — one of: run_pipeline, check_health, get_status
            - pipeline_context: dict — context passed through the pipeline
            - start_stage: str — stage to start from (default: lead_discovery)
        """
        action = context.get("action", "get_status")

        if action == "run_pipeline":
            return await self._run_pipeline(context, session)
        elif action == "check_health":
            return await self._check_health(context, session)
        elif action == "get_status":
            return self._get_status()
        else:
            return AgentResult(success=False, errors=[f"Unknown action: {action}"])

    async def _run_pipeline(
        self,
        context: dict[str, Any],
        session: AsyncSession | None,
    ) -> AgentResult:
        """Run agents in pipeline order, passing context through."""
        pipeline_context = context.get("pipeline_context", {})
        start_stage = context.get("start_stage", "lead_discovery")

        # Find starting index
        try:
            start_idx = PIPELINE_STAGES.index(start_stage)
        except ValueError:
            return AgentResult(
                success=False,
                errors=[f"Unknown stage: {start_stage}. Valid: {PIPELINE_STAGES}"],
            )

        results: list[dict[str, Any]] = []
        current_context = dict(pipeline_context)

        for stage_name in PIPELINE_STAGES[start_idx:]:
            agent = get_agent(stage_name)
            if not agent:
                logger.warning("pipeline_agent_missing", stage=stage_name)
                results.append({
                    "stage": stage_name,
                    "status": "skipped",
                    "reason": "agent not registered",
                })
                continue

            result = await agent.execute(current_context, session)
            results.append({
                "stage": stage_name,
                "status": "completed" if result.success else "failed",
                "data": result.data,
                "errors": result.errors,
            })

            if not result.success:
                logger.warning("pipeline_stage_failed", stage=stage_name, errors=result.errors)
                break

            # Pass output as input to next stage
            current_context.update(result.data)

        all_success = all(r["status"] == "completed" for r in results)
        return AgentResult(
            success=all_success,
            data={
                "pipeline_results": results,
                "stages_completed": sum(1 for r in results if r["status"] == "completed"),
                "stages_total": len(PIPELINE_STAGES) - start_idx,
            },
        )

    async def _check_health(
        self,
        context: dict[str, Any],
        session: AsyncSession | None,
    ) -> AgentResult:
        """Check health of all registered agents and campaign anomalies."""
        agents = list_agents()
        health: list[dict[str, Any]] = []

        for agent_info in agents:
            agent = get_agent(agent_info["name"])
            health.append({
                "name": agent_info["name"],
                "description": agent_info["description"],
                "registered": agent is not None,
            })

        # Check campaign anomalies if session available
        anomaly_summary = None
        if session:
            try:
                from src.services.anomaly_detection import check_all_campaigns
                anomaly_summary = await check_all_campaigns(session)
            except Exception as exc:
                logger.warning("health_check_anomaly_failed", error=str(exc))

        return AgentResult(
            success=True,
            data={
                "agents": health,
                "total_agents": len(health),
                "anomaly_summary": anomaly_summary,
            },
        )

    def _get_status(self) -> AgentResult:
        """Return current agent registry status."""
        agents = list_agents()
        return AgentResult(
            success=True,
            data={
                "pipeline_stages": PIPELINE_STAGES,
                "registered_agents": agents,
                "total_agents": len(agents),
            },
        )


# Auto-register
_agent = CoordinatorAgent()
register_agent(_agent)
