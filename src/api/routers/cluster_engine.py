"""Cluster Expansion Engine API (NIF-151 through NIF-159)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.db.session import get_session
from src.services.cluster_engine import (
    detect_clusters,
    identify_anchors,
    plan_expansion,
    estimate_flywheel,
    get_recommendations,
    launch_campaign,
    recalculate_cluster,
    record_feedback,
    get_cluster_history,
    list_clusters,
    get_cluster_detail,
)
from src.utils.logging import get_logger

logger = get_logger("cluster_engine_api")

router = APIRouter(
    prefix="/clusters",
    tags=["clusters"],
    dependencies=[Depends(require_auth)],
)


# -- Pydantic schemas ---------------------------------------------------------


class DetectBody(BaseModel):
    zip_code: str | None = None
    min_size: int = Field(default=3, ge=2, le=50)
    radius_miles: float = Field(default=1.0, gt=0, le=50.0)


class LaunchCampaignBody(BaseModel):
    campaign_type: str = Field(default="email", pattern="^(email|sms|phone|multi)$")


class FeedbackBody(BaseModel):
    feedback_type: str = Field(..., pattern="^(expansion_success|expansion_failure|quality_rating)$")
    details: dict | None = None
    submitted_by: str = "system"


# -- Helpers -------------------------------------------------------------------


def _cluster_to_dict(cluster) -> dict:
    return {
        "id": str(cluster.id),
        "name": cluster.name,
        "cluster_type": cluster.cluster_type,
        "zip_codes": cluster.zip_codes or [],
        "center_lat": cluster.center_lat,
        "center_lng": cluster.center_lng,
        "radius_miles": cluster.radius_miles,
        "restaurant_count": cluster.restaurant_count,
        "avg_icp_score": cluster.avg_icp_score,
        "flywheel_score": cluster.flywheel_score,
        "status": cluster.status,
        "detection_params": cluster.detection_params,
        "detected_at": cluster.detected_at.isoformat() if cluster.detected_at else None,
        "created_at": cluster.created_at.isoformat() if cluster.created_at else None,
        "updated_at": cluster.updated_at.isoformat() if cluster.updated_at else None,
    }


def _member_to_dict(member) -> dict:
    return {
        "id": str(member.id),
        "cluster_id": str(member.cluster_id),
        "restaurant_id": str(member.restaurant_id),
        "role": member.role,
        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        "icp_score_at_join": member.icp_score_at_join,
    }


def _plan_to_dict(plan) -> dict:
    return {
        "id": str(plan.id),
        "cluster_id": str(plan.cluster_id),
        "target_restaurant_id": str(plan.target_restaurant_id),
        "sequence_order": plan.sequence_order,
        "strategy": plan.strategy,
        "priority_score": plan.priority_score,
        "status": plan.status,
        "notes": plan.notes,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


def _history_to_dict(event) -> dict:
    return {
        "id": str(event.id),
        "cluster_id": str(event.cluster_id),
        "event_type": event.event_type,
        "details": event.details,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _feedback_to_dict(fb) -> dict:
    return {
        "id": str(fb.id),
        "cluster_id": str(fb.cluster_id),
        "feedback_type": fb.feedback_type,
        "details": fb.details,
        "submitted_by": fb.submitted_by,
        "created_at": fb.created_at.isoformat() if fb.created_at else None,
    }


def _campaign_to_dict(campaign) -> dict:
    return {
        "id": str(campaign.id),
        "name": campaign.name,
        "campaign_type": campaign.campaign_type,
        "status": campaign.status,
        "target_criteria": campaign.target_criteria,
        "created_by": campaign.created_by,
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
    }


# -- Endpoints -----------------------------------------------------------------


@router.post("/detect")
async def detect_clusters_endpoint(
    body: DetectBody,
    session: AsyncSession = Depends(get_session),
):
    """Detect merchant clusters in an area (NIF-151)."""
    clusters = await detect_clusters(
        session,
        zip_code=body.zip_code,
        min_size=body.min_size,
        radius_miles=body.radius_miles,
    )
    await session.commit()
    return [_cluster_to_dict(c) for c in clusters]


@router.get("")
async def list_clusters_endpoint(
    status: str | None = Query(None, pattern="^(detected|active|expanding|mature)$"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """List clusters with optional status filter."""
    clusters = await list_clusters(session, status=status, limit=limit)
    return [_cluster_to_dict(c) for c in clusters]


@router.get("/{cluster_id}")
async def get_cluster(
    cluster_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get cluster detail with members (NIF-155)."""
    cluster = await get_cluster_detail(session, cluster_id)
    if not cluster:
        raise HTTPException(404, "Cluster not found")
    result = _cluster_to_dict(cluster)
    result["members"] = [_member_to_dict(m) for m in cluster.members]
    return result


@router.post("/{cluster_id}/expansion-plan")
async def create_expansion_plan(
    cluster_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Create expansion plan for a cluster (NIF-153)."""
    try:
        # First identify anchors if needed
        await identify_anchors(session, cluster_id)
        plans = await plan_expansion(session, cluster_id)
        await session.commit()
        return [_plan_to_dict(p) for p in plans]
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{cluster_id}/recommendations")
async def get_cluster_recommendations(
    cluster_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get recommended actions for a cluster (NIF-156)."""
    try:
        recs = await get_recommendations(session, cluster_id)
        return recs
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{cluster_id}/launch-campaign")
async def launch_cluster_campaign(
    cluster_id: UUID,
    body: LaunchCampaignBody,
    session: AsyncSession = Depends(get_session),
):
    """Launch outreach campaign from cluster expansion plan (NIF-157)."""
    try:
        campaign = await launch_campaign(session, cluster_id, campaign_type=body.campaign_type)
        await session.commit()
        return _campaign_to_dict(campaign)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/{cluster_id}/recalculate")
async def recalculate_cluster_endpoint(
    cluster_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Recalculate cluster scores and stats (NIF-158)."""
    try:
        cluster = await recalculate_cluster(session, cluster_id)
        await session.commit()
        return _cluster_to_dict(cluster)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{cluster_id}/feedback")
async def submit_feedback(
    cluster_id: UUID,
    body: FeedbackBody,
    session: AsyncSession = Depends(get_session),
):
    """Submit feedback for a cluster (NIF-159)."""
    try:
        fb = await record_feedback(
            session,
            cluster_id=cluster_id,
            feedback_type=body.feedback_type,
            details=body.details,
            submitted_by=body.submitted_by,
        )
        await session.commit()
        return _feedback_to_dict(fb)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{cluster_id}/history")
async def get_history(
    cluster_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get cluster event history (NIF-158)."""
    try:
        events = await get_cluster_history(session, cluster_id)
        return [_history_to_dict(e) for e in events]
    except ValueError as exc:
        raise HTTPException(404, str(exc))
