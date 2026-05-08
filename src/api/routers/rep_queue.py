"""Sales Rep Work Queue API (NIF-145, NIF-146, NIF-147)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.db.session import get_session
from src.services.rep_queue import (
    get_queue,
    add_to_queue,
    claim_item,
    complete_item,
    skip_item,
    get_rep_ranking,
    populate_queue,
    get_next_best_action,
    enrich_queue_with_actions,
)
from src.utils.logging import get_logger

logger = get_logger("rep_queue_api")

router = APIRouter(
    prefix="/rep-queue",
    tags=["rep-queue"],
    dependencies=[Depends(require_auth)],
)


# -- Pydantic schemas ---------------------------------------------------------


class AddItemBody(BaseModel):
    rep_id: str
    restaurant_id: UUID
    lead_id: UUID | None = None
    reason: str | None = None
    context_data: dict | None = None


class ClaimBody(BaseModel):
    rep_id: str


class CompleteBody(BaseModel):
    outcome: str | None = None


class SkipBody(BaseModel):
    reason: str | None = None


class PopulateFilters(BaseModel):
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    min_icp_score: float | None = None
    fit_label: str | None = None
    is_chain: bool | None = None
    limit: int = Field(default=50, ge=1, le=200)


# -- Helpers -------------------------------------------------------------------


def _item_to_dict(item) -> dict:
    return {
        "id": str(item.id),
        "rep_id": item.rep_id,
        "restaurant_id": str(item.restaurant_id),
        "lead_id": str(item.lead_id) if item.lead_id else None,
        "priority_score": item.priority_score,
        "status": item.status,
        "reason": item.reason,
        "context_data": item.context_data,
        "claimed_at": item.claimed_at.isoformat() if item.claimed_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _ranking_to_dict(ranking) -> dict:
    return {
        "id": str(ranking.id),
        "rep_id": ranking.rep_id,
        "total_items": ranking.total_items,
        "completed_today": ranking.completed_today,
        "avg_completion_time_mins": ranking.avg_completion_time_mins,
        "active_items": ranking.active_items,
        "last_activity_at": ranking.last_activity_at.isoformat() if ranking.last_activity_at else None,
        "ranking_score": ranking.ranking_score,
        "created_at": ranking.created_at.isoformat() if ranking.created_at else None,
        "updated_at": ranking.updated_at.isoformat() if ranking.updated_at else None,
    }


# -- Endpoints -----------------------------------------------------------------


@router.get("/{rep_id}")
async def get_rep_queue(
    rep_id: str,
    status: str | None = Query(None, pattern="^(pending|claimed|completed|skipped)$"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Get ranked queue items for a rep (NIF-147)."""
    items = await get_queue(session, rep_id, status=status, limit=limit)
    return [_item_to_dict(i) for i in items]


@router.post("/items")
async def add_queue_item(
    body: AddItemBody,
    session: AsyncSession = Depends(get_session),
):
    """Add an item to a rep's queue (NIF-145)."""
    try:
        item = await add_to_queue(
            session,
            rep_id=body.rep_id,
            restaurant_id=body.restaurant_id,
            lead_id=body.lead_id,
            reason=body.reason,
            context_data=body.context_data,
        )
        await session.commit()
        return _item_to_dict(item)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.patch("/items/{item_id}/claim")
async def claim_queue_item(
    item_id: UUID,
    body: ClaimBody,
    session: AsyncSession = Depends(get_session),
):
    """Claim a queue item (NIF-147)."""
    try:
        item = await claim_item(session, item_id, rep_id=body.rep_id)
        await session.commit()
        return _item_to_dict(item)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.patch("/items/{item_id}/complete")
async def complete_queue_item(
    item_id: UUID,
    body: CompleteBody,
    session: AsyncSession = Depends(get_session),
):
    """Complete a queue item (NIF-147)."""
    try:
        item = await complete_item(session, item_id, outcome=body.outcome)
        await session.commit()
        return _item_to_dict(item)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.patch("/items/{item_id}/skip")
async def skip_queue_item(
    item_id: UUID,
    body: SkipBody,
    session: AsyncSession = Depends(get_session),
):
    """Skip a queue item (NIF-147)."""
    try:
        item = await skip_item(session, item_id, reason=body.reason)
        await session.commit()
        return _item_to_dict(item)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/{rep_id}/ranking")
async def get_ranking(
    rep_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get rep performance ranking (NIF-146)."""
    ranking = await get_rep_ranking(session, rep_id)
    await session.commit()
    return _ranking_to_dict(ranking)


@router.get("/{rep_id}/next-action")
async def get_rep_next_action(
    rep_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get highest-priority item with next-best-action recommendation (NIF-242)."""
    result = await get_next_best_action(session, rep_id)
    if not result:
        raise HTTPException(404, "No pending queue items for this rep")
    await session.commit()
    return result


@router.post("/{rep_id}/enrich-actions")
async def enrich_rep_queue_actions(
    rep_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Enrich all pending queue items with next-best-action recommendations (NIF-242)."""
    result = await enrich_queue_with_actions(session, rep_id)
    await session.commit()
    return result


@router.post("/{rep_id}/populate")
async def populate_rep_queue(
    rep_id: str,
    body: PopulateFilters,
    session: AsyncSession = Depends(get_session),
):
    """Auto-populate a rep's queue from restaurant filters (NIF-147)."""
    result = await populate_queue(
        session,
        rep_id=rep_id,
        filters=body.model_dump(exclude_none=True, exclude={"limit"}) or None,
        limit=body.limit,
    )
    await session.commit()
    return result
