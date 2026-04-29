"""Conversion feedback loop API (NIF-260).

Endpoints:
  GET  /feedback/report             — get conversion feedback report
  POST /feedback/suggest-adjustments — generate weight adjustment suggestions
  POST /feedback/apply              — apply approved adjustments to a scoring profile
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.db.session import get_session
from src.services.feedback_loop import (
    analyze_conversions,
    suggest_weight_adjustments,
    apply_adjustments,
    get_feedback_report,
)
from src.utils.logging import get_logger

logger = get_logger("feedback_loop_api")

router = APIRouter(
    prefix="/feedback",
    tags=["feedback"],
    dependencies=[Depends(require_auth)],
)


# -- Pydantic schemas ---------------------------------------------------------

class SuggestAdjustmentsRequest(BaseModel):
    period: str = Field(..., description="Period string, e.g. '2026-04' or '2026-W15'")
    profile_id: UUID | None = Field(None, description="Scoring profile ID (uses active default if omitted)")


class WeightAdjustment(BaseModel):
    signal: str = Field(..., description="Signal name")
    new_weight: float = Field(..., ge=0.0, le=50.0, description="New weight to apply")


class ApplyAdjustmentsRequest(BaseModel):
    profile_id: UUID = Field(..., description="Scoring profile to update")
    adjustments: list[WeightAdjustment] = Field(..., description="List of signal weight changes")
    approved_by: str = Field(default="system", description="User approving the changes")


# -- Endpoints ----------------------------------------------------------------

@router.get("/report")
async def feedback_report(
    period: str = Query(..., description="Period string, e.g. '2026-04' or '2026-W15'"),
    profile_id: UUID | None = Query(None, description="Scoring profile ID"),
    auth: dict = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    """Get conversion feedback report with score-to-conversion correlation."""
    return await get_feedback_report(session, period, profile_id)


@router.post("/suggest-adjustments")
async def suggest_adjustments(
    body: SuggestAdjustmentsRequest,
    auth: dict = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    """Generate weight adjustment suggestions based on conversion analysis."""
    return await suggest_weight_adjustments(session, body.period, body.profile_id)


@router.post("/apply")
async def apply_weight_adjustments(
    body: ApplyAdjustmentsRequest,
    auth: dict = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    """Apply approved weight adjustments to a scoring profile."""
    result = await apply_adjustments(
        session,
        profile_id=body.profile_id,
        adjustments=[a.model_dump() for a in body.adjustments],
        approved_by=body.approved_by,
    )
    if result.get("error") == "profile_not_found":
        raise HTTPException(status_code=404, detail="Scoring profile not found")
    return result
