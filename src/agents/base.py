"""Base agent class for the AI agent framework (NIF-269).

All agents inherit from BaseAgent and implement the `run` method.
The framework tracks execution via AgentRun records in the database.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AgentRun
from src.utils.logging import get_logger

logger = get_logger("agents.base")


@dataclass
class AgentResult:
    """Standard result envelope for agent executions."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "errors": self.errors,
            "metadata": self.metadata,
        }


class BaseAgent(ABC):
    """Abstract base for all AI agents.

    Subclasses must implement:
      - name (class attribute): unique agent identifier
      - description (class attribute): human-readable description
      - run(context, session): agent logic returning AgentResult
    """

    name: str = "base"
    description: str = "Base agent"

    async def execute(
        self,
        context: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> AgentResult:
        """Execute the agent with tracking. Wraps `run` with timing and logging."""
        run_record = None
        start = time.monotonic()

        if session:
            run_record = AgentRun(
                agent_name=self.name,
                status="running",
                input_context=context,
                started_at=datetime.now(timezone.utc),
            )
            session.add(run_record)
            await session.flush()

        try:
            result = await self.run(context, session)
            duration_ms = int((time.monotonic() - start) * 1000)

            if run_record and session:
                run_record.status = "completed"
                run_record.output_result = result.to_dict()
                run_record.duration_ms = duration_ms
                run_record.completed_at = datetime.now(timezone.utc)
                await session.flush()

            logger.info(
                "agent_completed",
                agent=self.name,
                success=result.success,
                duration_ms=duration_ms,
            )
            return result

        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            if run_record and session:
                run_record.status = "failed"
                run_record.error_message = str(exc)
                run_record.duration_ms = duration_ms
                run_record.completed_at = datetime.now(timezone.utc)
                await session.flush()

            logger.error("agent_failed", agent=self.name, error=str(exc))
            return AgentResult(success=False, errors=[str(exc)])

    @abstractmethod
    async def run(
        self,
        context: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> AgentResult:
        """Agent-specific logic. Subclasses must implement this."""
        ...


# ── Agent registry ───────────────────────────────────────────────

_registry: dict[str, BaseAgent] = {}


def register_agent(agent: BaseAgent) -> None:
    """Register an agent instance in the global registry."""
    _registry[agent.name] = agent
    logger.info("agent_registered", name=agent.name)


def get_agent(name: str) -> BaseAgent | None:
    """Look up a registered agent by name."""
    return _registry.get(name)


def list_agents() -> list[dict[str, str]]:
    """Return metadata for all registered agents."""
    return [
        {"name": a.name, "description": a.description}
        for a in _registry.values()
    ]
