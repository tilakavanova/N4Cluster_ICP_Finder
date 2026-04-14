"""AI Merchant Qualification API (NIF-142 through NIF-144)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.db.session import get_session
from src.services.qualification import (
    qualify_restaurant,
    get_latest_qualification,
    review_qualification,
    batch_qualify,
    list_pending_review,
)
from src.utils.logging import get_logger

logger = get_logger("qualification_api")

router = APIRouter(
    prefix="/qualification",
    tags=["qualification"],
    dependencies=[Depends(require_auth)],
)


# ── Pydantic schemas ───────────────────────────────────────────


class ReviewBody(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reviewed_by: str
    notes: str | None = None


class BatchFilters(BaseModel):
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    min_rating: float | None = None
    is_chain: bool | None = None


# ── Helpers ────────────────────────────────────────────────────


def _result_to_dict(qr, include_explanations: bool = False) -> dict:
    data = {
        "id": str(qr.id),
        "restaurant_id": str(qr.restaurant_id),
        "qualification_status": qr.qualification_status,
        "confidence_score": qr.confidence_score,
        "signals_summary": qr.signals_summary,
        "qualified_at": qr.qualified_at.isoformat() if qr.qualified_at else None,
        "expires_at": qr.expires_at.isoformat() if qr.expires_at else None,
        "model_version": qr.model_version,
        "reviewed_by": qr.reviewed_by,
        "reviewed_at": qr.reviewed_at.isoformat() if qr.reviewed_at else None,
        "review_decision": qr.review_decision,
        "review_notes": qr.review_notes,
        "created_at": qr.created_at.isoformat() if qr.created_at else None,
    }
    if include_explanations and hasattr(qr, "explanations") and qr.explanations:
        data["explanations"] = [
            {
                "id": str(e.id),
                "factor_name": e.factor_name,
                "factor_value": e.factor_value,
                "impact": e.impact,
                "weight": e.weight,
                "explanation_text": e.explanation_text,
            }
            for e in qr.explanations
        ]
    return data


# ── Endpoints ──────────────────────────────────────────────────


@router.post("/evaluate/{restaurant_id}")
async def evaluate_restaurant(
    restaurant_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Trigger AI qualification for a restaurant (NIF-142)."""
    try:
        result = await qualify_restaurant(session, restaurant_id)
        await session.commit()
        return _result_to_dict(result)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{restaurant_id}")
async def get_qualification(
    restaurant_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get latest qualification result with explanations (NIF-143)."""
    result = await get_latest_qualification(session, restaurant_id)
    if not result:
        raise HTTPException(404, "No qualification result found for this restaurant")
    return _result_to_dict(result, include_explanations=True)


@router.patch("/{result_id}/review")
async def review_result(
    result_id: UUID,
    body: ReviewBody,
    session: AsyncSession = Depends(get_session),
):
    """Human review override for a qualification (NIF-144)."""
    try:
        result = await review_qualification(
            session,
            result_id,
            decision=body.decision,
            reviewed_by=body.reviewed_by,
            notes=body.notes,
        )
        await session.commit()
        return _result_to_dict(result)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/batch")
async def batch_evaluate(
    body: BatchFilters,
    session: AsyncSession = Depends(get_session),
):
    """Batch qualify restaurants by filters (NIF-142)."""
    result = await batch_qualify(session, body.model_dump(exclude_none=True) or None)
    await session.commit()
    return result


@router.get("/pending-review/list")
async def pending_review(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """List qualification results needing human review (NIF-144)."""
    results = await list_pending_review(session, limit=limit, offset=offset)
    return [_result_to_dict(r, include_explanations=True) for r in results]
