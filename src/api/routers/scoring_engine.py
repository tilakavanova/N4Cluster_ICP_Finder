"""Configurable Scoring Engine API (NIF-125 through NIF-132)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_api_key
from src.db.models import (
    ScoringProfile, ScoringRule, ScoreExplanation,
    ScoreVersion, ScoringConfigLink, ScoreRecalcJob,
)
from src.db.session import get_session
from src.services.scoring_engine import (
    evaluate_restaurant,
    explain_score,
    recalculate_batch,
    create_version_snapshot,
)
from src.utils.logging import get_logger

logger = get_logger("scoring_engine_api")

router = APIRouter(
    prefix="/scoring-engine",
    tags=["scoring-engine"],
    dependencies=[Depends(require_api_key)],
)


# ── Pydantic schemas ───────────────────────────────────────────


class SignalConfig(BaseModel):
    name: str
    weight: float = Field(ge=0, le=100)
    type: str = "numeric"
    enabled: bool = True


class ProfileCreate(BaseModel):
    name: str = Field(max_length=100)
    description: str | None = None
    signals: list[SignalConfig]


class ProfileUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    signals: list[SignalConfig] | None = None
    is_active: bool | None = None


class RuleCreate(BaseModel):
    signal_name: str
    rule_type: str = Field(pattern="^(threshold|range|boolean|custom)$")
    condition: dict = {}
    points: float = 0.0
    description: str | None = None


class ConfigLinkCreate(BaseModel):
    entity_type: str = Field(pattern="^(market|cuisine|chain_group)$")
    entity_value: str


# ── Profile CRUD ───────────────────────────────────────────────


@router.post("/profiles")
async def create_profile(body: ProfileCreate, session: AsyncSession = Depends(get_session)):
    """Create a new scoring profile (NIF-125)."""
    profile = ScoringProfile(
        name=body.name,
        description=body.description,
        signals=[s.model_dump() for s in body.signals],
    )
    session.add(profile)
    await session.flush()
    await session.commit()
    return _profile_to_dict(profile)


@router.get("/profiles")
async def list_profiles(
    active_only: bool = Query(False),
    session: AsyncSession = Depends(get_session),
):
    """List scoring profiles."""
    query = select(ScoringProfile).order_by(ScoringProfile.created_at.desc())
    if active_only:
        query = query.where(ScoringProfile.is_active == True)  # noqa: E712
    result = await session.execute(query)
    return [_profile_to_dict(p) for p in result.scalars().all()]


@router.get("/profiles/{profile_id}")
async def get_profile(profile_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get a scoring profile by ID."""
    profile = await session.get(ScoringProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Scoring profile not found")
    return _profile_to_dict(profile)


@router.patch("/profiles/{profile_id}")
async def update_profile(
    profile_id: UUID,
    body: ProfileUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update a scoring profile, bumping version (NIF-129)."""
    profile = await session.get(ScoringProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Scoring profile not found")

    changes = {}
    if body.name is not None and body.name != profile.name:
        changes["name"] = {"old": profile.name, "new": body.name}
        profile.name = body.name
    if body.description is not None and body.description != profile.description:
        changes["description"] = {"old": profile.description, "new": body.description}
        profile.description = body.description
    if body.signals is not None:
        changes["signals"] = {"old": profile.signals, "new": [s.model_dump() for s in body.signals]}
        profile.signals = [s.model_dump() for s in body.signals]
    if body.is_active is not None and body.is_active != profile.is_active:
        changes["is_active"] = {"old": profile.is_active, "new": body.is_active}
        profile.is_active = body.is_active

    if changes:
        profile.version += 1
        await create_version_snapshot(session, profile, changes)

    await session.commit()
    return _profile_to_dict(profile)


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: UUID, session: AsyncSession = Depends(get_session)):
    """Delete a scoring profile."""
    profile = await session.get(ScoringProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Scoring profile not found")
    await session.delete(profile)
    await session.commit()
    return {"deleted": True}


# ── Rules ──────────────────────────────────────────────────────


@router.post("/profiles/{profile_id}/rules")
async def add_rule(
    profile_id: UUID,
    body: RuleCreate,
    session: AsyncSession = Depends(get_session),
):
    """Add a scoring rule to a profile (NIF-126)."""
    profile = await session.get(ScoringProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Scoring profile not found")

    rule = ScoringRule(
        profile_id=profile_id,
        signal_name=body.signal_name,
        rule_type=body.rule_type,
        condition=body.condition,
        points=body.points,
        description=body.description,
    )
    session.add(rule)
    await session.commit()
    return {
        "id": str(rule.id),
        "profile_id": str(profile_id),
        "signal_name": rule.signal_name,
        "rule_type": rule.rule_type,
        "condition": rule.condition,
        "points": rule.points,
        "description": rule.description,
    }


@router.get("/profiles/{profile_id}/rules")
async def list_rules(profile_id: UUID, session: AsyncSession = Depends(get_session)):
    """List rules for a profile."""
    result = await session.execute(
        select(ScoringRule).where(ScoringRule.profile_id == profile_id)
    )
    return [
        {
            "id": str(r.id),
            "signal_name": r.signal_name,
            "rule_type": r.rule_type,
            "condition": r.condition,
            "points": r.points,
            "description": r.description,
        }
        for r in result.scalars().all()
    ]


# ── Scoring & Explanations ─────────────────────────────────────


@router.post("/score/{restaurant_id}")
async def score_restaurant(
    restaurant_id: UUID,
    profile_id: UUID = Query(..., description="Scoring profile to use"),
    session: AsyncSession = Depends(get_session),
):
    """Score a restaurant using a configurable profile (NIF-128)."""
    try:
        exp = await evaluate_restaurant(session, restaurant_id, profile_id)
        await session.commit()
        return {
            "restaurant_id": str(exp.restaurant_id),
            "profile_id": str(exp.profile_id),
            "total_score": exp.total_score,
            "fit_label": exp.fit_label,
            "signal_breakdown": exp.signal_breakdown,
            "explanation_text": exp.explanation_text,
        }
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/explanations/{restaurant_id}")
async def get_explanation(
    restaurant_id: UUID,
    profile_id: UUID = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Get score explanation for a restaurant (NIF-127)."""
    result = await explain_score(session, restaurant_id, profile_id)
    if not result:
        raise HTTPException(404, "No score explanation found. Score the restaurant first.")
    return result


# ── Recalculation Jobs ──────────────────────────────────────────


@router.post("/recalculate")
async def trigger_recalculation(
    profile_id: UUID = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Trigger batch recalculation for a profile (NIF-132)."""
    try:
        job = await recalculate_batch(session, profile_id)
        await session.commit()
        return {
            "job_id": str(job.id),
            "profile_id": str(job.profile_id),
            "status": job.status,
            "total_items": job.total_items,
            "processed_items": job.processed_items,
        }
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/recalculate/{job_id}")
async def get_recalc_status(job_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get recalculation job status (NIF-132)."""
    job = await session.get(ScoreRecalcJob, job_id)
    if not job:
        raise HTTPException(404, "Recalculation job not found")
    return {
        "job_id": str(job.id),
        "profile_id": str(job.profile_id),
        "status": job.status,
        "total_items": job.total_items,
        "processed_items": job.processed_items,
        "error_message": job.error_message,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


# ── Config Links ───────────────────────────────────────────────


@router.post("/profiles/{profile_id}/links")
async def add_config_link(
    profile_id: UUID,
    body: ConfigLinkCreate,
    session: AsyncSession = Depends(get_session),
):
    """Link a profile to a market/cuisine/chain_group (NIF-130)."""
    profile = await session.get(ScoringProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Scoring profile not found")

    link = ScoringConfigLink(
        profile_id=profile_id,
        entity_type=body.entity_type,
        entity_value=body.entity_value,
    )
    session.add(link)
    await session.commit()
    return {
        "id": str(link.id),
        "profile_id": str(profile_id),
        "entity_type": link.entity_type,
        "entity_value": link.entity_value,
    }


@router.get("/profiles/{profile_id}/links")
async def list_config_links(profile_id: UUID, session: AsyncSession = Depends(get_session)):
    """List config links for a profile."""
    result = await session.execute(
        select(ScoringConfigLink).where(ScoringConfigLink.profile_id == profile_id)
    )
    return [
        {
            "id": str(l.id),
            "entity_type": l.entity_type,
            "entity_value": l.entity_value,
        }
        for l in result.scalars().all()
    ]


# ── Version History ─────────────────────────────────────────────


@router.get("/profiles/{profile_id}/versions")
async def list_versions(profile_id: UUID, session: AsyncSession = Depends(get_session)):
    """List version history for a profile (NIF-129)."""
    result = await session.execute(
        select(ScoreVersion)
        .where(ScoreVersion.profile_id == profile_id)
        .order_by(ScoreVersion.version_number.desc())
    )
    return [
        {
            "id": str(v.id),
            "version_number": v.version_number,
            "changes": v.changes,
            "created_by": v.created_by,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in result.scalars().all()
    ]


# ── Helpers ─────────────────────────────────────────────────────


def _profile_to_dict(p: ScoringProfile) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "version": p.version,
        "description": p.description,
        "signals": p.signals,
        "is_active": p.is_active,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
