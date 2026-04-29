"""A/B testing API endpoints (NIF-238, NIF-262).

POST /ab-tests               — create experiment
GET  /ab-tests               — list experiments
POST /ab-tests/{id}/start    — start experiment
GET  /ab-tests/{id}/results  — get results
POST /ab-tests/{id}/declare-winner — declare winner
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ABExperiment
from src.db.session import get_session
from src.services.ab_testing import ABTestService
from src.utils.logging import get_logger

logger = get_logger("ab_testing_api")

router = APIRouter(prefix="/ab-tests", tags=["ab-testing"])


# ── Request / response schemas ───────────────────────────────

class CreateExperimentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    variants: list[dict] = Field(..., min_length=2)
    metric: str = Field(..., pattern=r"^(open_rate|click_rate|reply_rate|conversion_rate)$")
    sample_size: int = Field(..., ge=1, le=100000)
    experiment_type: str = Field(default="template", pattern=r"^(template|scoring_profile)$")


class CreateScoringExperimentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    profile_a_id: UUID
    profile_b_id: UUID
    metric: str = Field(default="conversion_rate", pattern=r"^(open_rate|click_rate|reply_rate|conversion_rate)$")


class RecordOutcomeRequest(BaseModel):
    lead_id: UUID
    metric_value: float = Field(..., ge=0.0, le=1.0)


class AssignVariantRequest(BaseModel):
    lead_id: UUID


# ── Endpoints ────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_experiment(
    body: CreateExperimentRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a new A/B test experiment."""
    svc = ABTestService(session)
    try:
        exp = await svc.create_experiment(
            name=body.name,
            variants=body.variants,
            metric=body.metric,
            sample_size=body.sample_size,
            experiment_type=body.experiment_type,
        )
        return {
            "id": str(exp.id),
            "name": exp.name,
            "status": exp.status,
            "experiment_type": exp.experiment_type,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/scoring", status_code=201)
async def create_scoring_experiment(
    body: CreateScoringExperimentRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create an A/B test between two scoring profiles (NIF-262)."""
    svc = ABTestService(session)
    try:
        exp = await svc.create_scoring_experiment(
            name=body.name,
            profile_a_id=body.profile_a_id,
            profile_b_id=body.profile_b_id,
            metric=body.metric,
        )
        return {
            "id": str(exp.id),
            "name": exp.name,
            "status": exp.status,
            "experiment_type": exp.experiment_type,
            "variants": exp.variants,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
async def list_experiments(
    status: str | None = None,
    experiment_type: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """List all A/B test experiments, optionally filtered by status or type."""
    query = select(ABExperiment).order_by(ABExperiment.created_at.desc())
    if status:
        query = query.where(ABExperiment.status == status)
    if experiment_type:
        query = query.where(ABExperiment.experiment_type == experiment_type)
    result = await session.execute(query)
    experiments = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "name": e.name,
            "experiment_type": e.experiment_type,
            "status": e.status,
            "metric": e.metric,
            "sample_size": e.sample_size,
            "winner_variant": e.winner_variant,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in experiments
    ]


@router.post("/{experiment_id}/start")
async def start_experiment(
    experiment_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Start a draft experiment."""
    svc = ABTestService(session)
    try:
        exp = await svc.start_experiment(experiment_id)
        return {"id": str(exp.id), "status": exp.status}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{experiment_id}/assign")
async def assign_variant(
    experiment_id: UUID,
    body: AssignVariantRequest,
    session: AsyncSession = Depends(get_session),
):
    """Assign a lead to a variant."""
    svc = ABTestService(session)
    try:
        assignment = await svc.assign_variant(experiment_id, body.lead_id)
        return {
            "id": str(assignment.id),
            "variant_name": assignment.variant_name,
            "lead_id": str(assignment.lead_id),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{experiment_id}/record-outcome")
async def record_outcome(
    experiment_id: UUID,
    body: RecordOutcomeRequest,
    session: AsyncSession = Depends(get_session),
):
    """Record the outcome metric for a lead."""
    svc = ABTestService(session)
    try:
        assignment = await svc.record_outcome(experiment_id, body.lead_id, body.metric_value)
        return {
            "id": str(assignment.id),
            "variant_name": assignment.variant_name,
            "outcome": assignment.outcome,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{experiment_id}/results")
async def get_results(
    experiment_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get per-variant statistics for an experiment."""
    svc = ABTestService(session)
    try:
        return await svc.get_results(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{experiment_id}/declare-winner")
async def declare_winner(
    experiment_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Run statistical significance check and declare winner if p < 0.05."""
    svc = ABTestService(session)
    try:
        return await svc.declare_winner(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
