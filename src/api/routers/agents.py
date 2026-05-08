"""AI Agent Framework API (NIF-269 through NIF-274).

Provides endpoints to run agents, check status, and submit RLHF feedback.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.db.session import get_session

# Import all agents to trigger registration
from src.agents.base import get_agent, list_agents  # noqa: E402
import src.agents.lead_discovery  # noqa: F401
import src.agents.qualification  # noqa: F401
import src.agents.outreach  # noqa: F401
import src.agents.closing  # noqa: F401
import src.agents.coordinator  # noqa: F401

from src.services.rlhf_feedback import record_feedback, get_feedback_summary
from src.utils.logging import get_logger

logger = get_logger("agents_api")

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    dependencies=[Depends(require_auth)],
)


# ── Pydantic schemas ──────────────────────────────────────────────


class AgentRunRequest(BaseModel):
    context: dict = Field(default_factory=dict, description="Context to pass to the agent")


class FeedbackRequest(BaseModel):
    input_context: dict = Field(default_factory=dict)
    output_result: dict = Field(default_factory=dict)
    rating: int = Field(ge=1, le=5, description="Rating from 1 (poor) to 5 (excellent)")
    feedback_text: str | None = None
    rated_by: str = "anonymous"
    run_id: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("")
async def list_available_agents():
    """List all registered agents."""
    return {"agents": list_agents()}


@router.post("/{agent_name}/run")
async def run_agent(
    agent_name: str,
    body: AgentRunRequest,
    session: AsyncSession = Depends(get_session),
):
    """Trigger an agent with the given context."""
    agent = get_agent(agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    result = await agent.execute(body.context, session)
    await session.commit()

    return {
        "agent": agent_name,
        "result": result.to_dict(),
    }


@router.get("/{agent_name}/status")
async def agent_status(
    agent_name: str,
    session: AsyncSession = Depends(get_session),
):
    """Get the last run status of an agent."""
    agent = get_agent(agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    from sqlalchemy import select
    from src.db.models import AgentRun

    q = (
        select(AgentRun)
        .where(AgentRun.agent_name == agent_name)
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    result = await session.execute(q)
    last_run = result.scalar_one_or_none()

    if not last_run:
        return {
            "agent": agent_name,
            "description": agent.description,
            "last_run": None,
        }

    return {
        "agent": agent_name,
        "description": agent.description,
        "last_run": {
            "id": str(last_run.id),
            "status": last_run.status,
            "started_at": last_run.started_at.isoformat() if last_run.started_at else None,
            "completed_at": last_run.completed_at.isoformat() if last_run.completed_at else None,
            "duration_ms": last_run.duration_ms,
        },
    }


@router.post("/{agent_name}/feedback")
async def submit_feedback(
    agent_name: str,
    body: FeedbackRequest,
    session: AsyncSession = Depends(get_session),
):
    """Submit RLHF feedback for an agent output."""
    agent = get_agent(agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    run_id = UUID(body.run_id) if body.run_id else None

    feedback = await record_feedback(
        session=session,
        agent_name=agent_name,
        input_context=body.input_context,
        output_result=body.output_result,
        rating=body.rating,
        feedback_text=body.feedback_text,
        rated_by=body.rated_by,
        run_id=run_id,
    )
    await session.commit()

    return {
        "id": str(feedback.id),
        "agent_name": agent_name,
        "rating": feedback.rating,
        "created_at": feedback.created_at.isoformat(),
    }


@router.get("/{agent_name}/feedback/summary")
async def feedback_summary(
    agent_name: str,
    session: AsyncSession = Depends(get_session),
):
    """Get feedback summary for an agent."""
    agent = get_agent(agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    return await get_feedback_summary(session, agent_name)
