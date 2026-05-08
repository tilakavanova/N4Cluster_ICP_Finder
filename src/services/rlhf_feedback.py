"""RLHF feedback collection from sales rep ratings (NIF-274).

Records structured feedback on agent outputs so the system can learn
from human preferences over time.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AgentFeedback, AgentRun
from src.utils.logging import get_logger

logger = get_logger("rlhf_feedback")

VALID_RATINGS = {1, 2, 3, 4, 5}


async def record_feedback(
    session: AsyncSession,
    agent_name: str,
    input_context: dict[str, Any],
    output_result: dict[str, Any],
    rating: int,
    feedback_text: str | None = None,
    rated_by: str = "anonymous",
    run_id: UUID | None = None,
) -> AgentFeedback:
    """Record RLHF feedback for an agent output.

    Args:
        session: Database session.
        agent_name: Name of the agent being rated.
        input_context: The input context that was given to the agent.
        output_result: The output the agent produced.
        rating: 1-5 star rating (1=poor, 5=excellent).
        feedback_text: Optional free-text feedback.
        rated_by: Identifier of the person providing feedback.
        run_id: Optional link to specific AgentRun record.

    Returns:
        The created AgentFeedback record.
    """
    if rating not in VALID_RATINGS:
        raise ValueError(f"Rating must be 1-5, got {rating}")

    feedback = AgentFeedback(
        agent_name=agent_name,
        run_id=run_id,
        input_context=input_context,
        output_result=output_result,
        rating=rating,
        feedback_text=feedback_text,
        rated_by=rated_by,
    )
    session.add(feedback)
    await session.flush()

    logger.info(
        "feedback_recorded",
        agent_name=agent_name,
        rating=rating,
        rated_by=rated_by,
    )
    return feedback


async def get_feedback_summary(
    session: AsyncSession,
    agent_name: str,
) -> dict[str, Any]:
    """Get feedback summary stats for an agent."""
    q = (
        select(
            func.count().label("total"),
            func.avg(AgentFeedback.rating).label("avg_rating"),
            func.min(AgentFeedback.rating).label("min_rating"),
            func.max(AgentFeedback.rating).label("max_rating"),
        )
        .where(AgentFeedback.agent_name == agent_name)
    )
    row = (await session.execute(q)).one()

    return {
        "agent_name": agent_name,
        "total_ratings": row.total or 0,
        "avg_rating": round(float(row.avg_rating), 2) if row.avg_rating else None,
        "min_rating": row.min_rating,
        "max_rating": row.max_rating,
    }


async def list_feedback(
    session: AsyncSession,
    agent_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AgentFeedback]:
    """List feedback entries, optionally filtered by agent name."""
    q = select(AgentFeedback).order_by(AgentFeedback.created_at.desc())
    if agent_name:
        q = q.where(AgentFeedback.agent_name == agent_name)
    q = q.limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())
