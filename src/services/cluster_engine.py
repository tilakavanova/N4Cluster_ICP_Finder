"""Cluster Expansion Engine (NIF-151 through NIF-159)."""

from datetime import datetime, timezone
from uuid import UUID

import numpy as np
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import (
    MerchantCluster, ClusterMember, ClusterExpansionPlan,
    ClusterHistory, ClusterFeedback,
    Restaurant, ICPScore, OutreachCampaign, OutreachTarget,
)
from src.scoring.geo_density import haversine_distance
from src.utils.logging import get_logger

logger = get_logger("cluster_engine")

# Constants
KM_PER_MILE = 1.60934
FLYWHEEL_WEIGHTS = {
    "density": 0.25,
    "avg_icp": 0.25,
    "anchor_strength": 0.20,
    "expansion_potential": 0.15,
    "feedback_score": 0.15,
}


async def _record_event(
    session: AsyncSession,
    cluster_id: UUID,
    event_type: str,
    details: dict | None = None,
) -> ClusterHistory:
    """Internal helper to record a cluster history event."""
    event = ClusterHistory(
        cluster_id=cluster_id,
        event_type=event_type,
        details=details or {},
    )
    session.add(event)
    await session.flush()
    return event


async def detect_clusters(
    session: AsyncSession,
    zip_code: str | None = None,
    min_size: int = 3,
    radius_miles: float = 1.0,
) -> list[MerchantCluster]:
    """Detect merchant clusters using geo density (NIF-151).

    Finds groups of nearby restaurants and creates cluster records.
    """
    query = select(Restaurant).outerjoin(ICPScore, ICPScore.restaurant_id == Restaurant.id)
    if zip_code:
        query = query.where(Restaurant.zip_code == zip_code)
    query = query.where(Restaurant.lat.isnot(None), Restaurant.lng.isnot(None))

    result = await session.execute(query)
    restaurants = list(result.scalars().all())

    if len(restaurants) < min_size:
        logger.info("too_few_restaurants", count=len(restaurants), min_size=min_size)
        return []

    radius_km = radius_miles * KM_PER_MILE

    # Simple clustering: group restaurants within radius of each other
    assigned = set()
    clusters_created = []

    for i, anchor in enumerate(restaurants):
        if anchor.id in assigned:
            continue

        # Find neighbors within radius
        neighbors = []
        for j, other in enumerate(restaurants):
            if other.id in assigned or other.id == anchor.id:
                continue
            dist = haversine_distance(anchor.lat, anchor.lng, other.lat, other.lng)
            if dist <= radius_km:
                neighbors.append(other)

        if len(neighbors) + 1 < min_size:
            continue

        # Create cluster
        group = [anchor] + neighbors
        assigned.add(anchor.id)
        for n in neighbors:
            assigned.add(n.id)

        lats = [r.lat for r in group]
        lngs = [r.lng for r in group]
        center_lat = float(np.mean(lats))
        center_lng = float(np.mean(lngs))

        zip_codes_set = list({r.zip_code for r in group if r.zip_code})

        cluster = MerchantCluster(
            name=f"Cluster {zip_code or 'auto'}-{len(clusters_created) + 1}",
            cluster_type="geographic",
            zip_codes=zip_codes_set,
            center_lat=center_lat,
            center_lng=center_lng,
            radius_miles=radius_miles,
            restaurant_count=len(group),
            avg_icp_score=0.0,
            flywheel_score=0.0,
            status="detected",
            detection_params={
                "zip_code": zip_code,
                "min_size": min_size,
                "radius_miles": radius_miles,
            },
        )
        session.add(cluster)
        await session.flush()

        # Add members
        for r in group:
            # Get ICP score for this restaurant
            icp_result = await session.execute(
                select(ICPScore.total_icp_score).where(ICPScore.restaurant_id == r.id)
            )
            icp_score = icp_result.scalar() or 0.0

            member = ClusterMember(
                cluster_id=cluster.id,
                restaurant_id=r.id,
                role="member",
                icp_score_at_join=icp_score,
            )
            session.add(member)

        await session.flush()

        # Calculate avg ICP score
        avg_result = await session.execute(
            select(func.avg(ClusterMember.icp_score_at_join)).where(
                ClusterMember.cluster_id == cluster.id
            )
        )
        cluster.avg_icp_score = round(float(avg_result.scalar() or 0.0), 4)

        await _record_event(session, cluster.id, "detected", {
            "restaurant_count": len(group),
            "zip_codes": zip_codes_set,
        })

        clusters_created.append(cluster)

    await session.flush()
    logger.info("clusters_detected", count=len(clusters_created))
    return clusters_created


async def identify_anchors(
    session: AsyncSession,
    cluster_id: UUID,
) -> list[ClusterMember]:
    """Identify anchor merchants in a cluster by highest ICP score (NIF-152)."""
    cluster = await session.get(MerchantCluster, cluster_id)
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found")

    members_result = await session.execute(
        select(ClusterMember)
        .where(ClusterMember.cluster_id == cluster_id)
        .order_by(ClusterMember.icp_score_at_join.desc())
    )
    members = list(members_result.scalars().all())

    if not members:
        return []

    # Top 20% (minimum 1) become anchors
    anchor_count = max(1, len(members) // 5)
    anchors = []

    for i, member in enumerate(members):
        if i < anchor_count:
            member.role = "anchor"
            anchors.append(member)
        elif member.role == "anchor":
            member.role = "member"

    await session.flush()

    for anchor in anchors:
        await _record_event(session, cluster_id, "member_added", {
            "restaurant_id": str(anchor.restaurant_id),
            "role": "anchor",
            "icp_score": anchor.icp_score_at_join,
        })

    logger.info("anchors_identified", cluster=str(cluster_id), count=len(anchors))
    return anchors


async def plan_expansion(
    session: AsyncSession,
    cluster_id: UUID,
) -> list[ClusterExpansionPlan]:
    """Create expansion plan targeting nearby prospect restaurants (NIF-153)."""
    cluster = await session.get(MerchantCluster, cluster_id)
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found")

    # Get existing member restaurant IDs
    member_result = await session.execute(
        select(ClusterMember.restaurant_id).where(ClusterMember.cluster_id == cluster_id)
    )
    member_ids = {row[0] for row in member_result.all()}

    # Find nearby restaurants not already in the cluster
    radius_km = cluster.radius_miles * KM_PER_MILE * 1.5  # search 1.5x cluster radius

    candidate_query = (
        select(Restaurant, ICPScore.total_icp_score)
        .outerjoin(ICPScore, ICPScore.restaurant_id == Restaurant.id)
        .where(
            Restaurant.lat.isnot(None),
            Restaurant.lng.isnot(None),
            Restaurant.id.notin_(member_ids) if member_ids else Restaurant.id.isnot(None),
        )
    )

    if cluster.zip_codes:
        candidate_query = candidate_query.where(Restaurant.zip_code.in_(cluster.zip_codes))

    result = await session.execute(candidate_query)
    candidates = result.all()

    # Score and filter candidates by distance
    scored_candidates = []
    for restaurant, icp_score in candidates:
        if not restaurant.lat or not restaurant.lng:
            continue
        dist = haversine_distance(
            cluster.center_lat, cluster.center_lng,
            restaurant.lat, restaurant.lng,
        )
        if dist <= radius_km:
            priority = float(icp_score or 0.0) * 0.7 + (1.0 - dist / radius_km) * 30.0
            scored_candidates.append((restaurant, priority, icp_score or 0.0))

    # Sort by priority descending
    scored_candidates.sort(key=lambda x: x[1], reverse=True)

    plans = []
    for seq, (restaurant, priority, icp_score) in enumerate(scored_candidates, start=1):
        plan = ClusterExpansionPlan(
            cluster_id=cluster_id,
            target_restaurant_id=restaurant.id,
            sequence_order=seq,
            strategy=f"Proximity outreach (ICP: {icp_score:.1f})",
            priority_score=round(priority, 2),
            status="planned",
            notes=f"Target: {restaurant.name}",
        )
        session.add(plan)
        plans.append(plan)

        # Also add as prospect member
        prospect = ClusterMember(
            cluster_id=cluster_id,
            restaurant_id=restaurant.id,
            role="prospect",
            icp_score_at_join=icp_score,
        )
        session.add(prospect)

    if plans:
        cluster.status = "expanding"

    await session.flush()
    await _record_event(session, cluster_id, "expanded", {
        "plans_created": len(plans),
    })

    logger.info("expansion_planned", cluster=str(cluster_id), plans=len(plans))
    return plans


async def estimate_flywheel(
    session: AsyncSession,
    cluster_id: UUID,
) -> float:
    """Calculate flywheel potential score for a cluster (NIF-154)."""
    cluster = await session.get(MerchantCluster, cluster_id)
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found")

    # Density component: restaurant count normalized (cap at 20)
    density = min(cluster.restaurant_count / 20.0, 1.0)

    # Average ICP score normalized to 0-1 (assuming max 100)
    avg_icp = min((cluster.avg_icp_score or 0.0) / 100.0, 1.0)

    # Anchor strength: ratio of anchors to total members
    member_result = await session.execute(
        select(
            func.count(ClusterMember.id).filter(ClusterMember.role == "anchor"),
            func.count(ClusterMember.id),
        ).where(ClusterMember.cluster_id == cluster_id)
    )
    row = member_result.one()
    anchor_count = row[0] or 0
    total_members = row[1] or 1
    anchor_strength = min(anchor_count / max(total_members * 0.2, 1), 1.0)

    # Expansion potential: planned items ratio
    plan_result = await session.execute(
        select(func.count(ClusterExpansionPlan.id)).where(
            and_(
                ClusterExpansionPlan.cluster_id == cluster_id,
                ClusterExpansionPlan.status == "planned",
            )
        )
    )
    planned_count = plan_result.scalar() or 0
    expansion_potential = min(planned_count / 10.0, 1.0)

    # Feedback score: positive feedback ratio
    feedback_result = await session.execute(
        select(
            func.count(ClusterFeedback.id).filter(ClusterFeedback.feedback_type == "expansion_success"),
            func.count(ClusterFeedback.id),
        ).where(ClusterFeedback.cluster_id == cluster_id)
    )
    fb_row = feedback_result.one()
    positive_fb = fb_row[0] or 0
    total_fb = fb_row[1] or 0
    feedback_score = (positive_fb / total_fb) if total_fb > 0 else 0.5  # default neutral

    # Weighted flywheel score
    flywheel = (
        density * FLYWHEEL_WEIGHTS["density"]
        + avg_icp * FLYWHEEL_WEIGHTS["avg_icp"]
        + anchor_strength * FLYWHEEL_WEIGHTS["anchor_strength"]
        + expansion_potential * FLYWHEEL_WEIGHTS["expansion_potential"]
        + feedback_score * FLYWHEEL_WEIGHTS["feedback_score"]
    )
    flywheel = round(min(flywheel, 1.0) * 100.0, 2)

    cluster.flywheel_score = flywheel
    await session.flush()

    logger.info("flywheel_estimated", cluster=str(cluster_id), score=flywheel)
    return flywheel


async def get_recommendations(
    session: AsyncSession,
    cluster_id: UUID,
) -> list[dict]:
    """Get recommended next actions for a cluster (NIF-156)."""
    cluster = await session.get(MerchantCluster, cluster_id)
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found")

    recommendations = []

    # Check if anchors have been identified
    anchor_result = await session.execute(
        select(func.count(ClusterMember.id)).where(
            and_(ClusterMember.cluster_id == cluster_id, ClusterMember.role == "anchor")
        )
    )
    anchor_count = anchor_result.scalar() or 0

    if anchor_count == 0:
        recommendations.append({
            "action": "identify_anchors",
            "priority": "high",
            "description": "Identify anchor merchants to serve as cluster foundation.",
        })

    # Check if expansion plan exists
    plan_result = await session.execute(
        select(func.count(ClusterExpansionPlan.id)).where(
            ClusterExpansionPlan.cluster_id == cluster_id
        )
    )
    plan_count = plan_result.scalar() or 0

    if plan_count == 0 and anchor_count > 0:
        recommendations.append({
            "action": "plan_expansion",
            "priority": "high",
            "description": "Create expansion plan to grow the cluster.",
        })

    # Check flywheel score
    if cluster.flywheel_score < 30.0:
        recommendations.append({
            "action": "improve_flywheel",
            "priority": "medium",
            "description": f"Flywheel score is low ({cluster.flywheel_score}). Add more high-ICP merchants.",
        })

    # Check if campaign should be launched
    planned_result = await session.execute(
        select(func.count(ClusterExpansionPlan.id)).where(
            and_(
                ClusterExpansionPlan.cluster_id == cluster_id,
                ClusterExpansionPlan.status == "planned",
            )
        )
    )
    planned_count = planned_result.scalar() or 0

    if planned_count >= 3:
        recommendations.append({
            "action": "launch_campaign",
            "priority": "medium",
            "description": f"{planned_count} expansion targets ready. Consider launching a campaign.",
        })

    # Check if recalculation is needed
    if cluster.status == "expanding":
        recommendations.append({
            "action": "recalculate",
            "priority": "low",
            "description": "Cluster is expanding — recalculate scores for updated metrics.",
        })

    logger.info("recommendations_generated", cluster=str(cluster_id), count=len(recommendations))
    return recommendations


async def launch_campaign(
    session: AsyncSession,
    cluster_id: UUID,
    campaign_type: str = "email",
) -> OutreachCampaign:
    """Create an outreach campaign from a cluster's expansion plan (NIF-157)."""
    cluster = await session.get(MerchantCluster, cluster_id)
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found")

    # Get planned expansion targets
    plan_result = await session.execute(
        select(ClusterExpansionPlan).where(
            and_(
                ClusterExpansionPlan.cluster_id == cluster_id,
                ClusterExpansionPlan.status == "planned",
            )
        ).order_by(ClusterExpansionPlan.sequence_order)
    )
    plans = list(plan_result.scalars().all())

    if not plans:
        raise ValueError("No planned expansion targets to campaign")

    # Create outreach campaign
    campaign = OutreachCampaign(
        name=f"Cluster Expansion: {cluster.name}",
        campaign_type=campaign_type,
        status="active",
        target_criteria={
            "cluster_id": str(cluster_id),
            "cluster_name": cluster.name,
            "target_count": len(plans),
        },
        created_by="cluster_engine",
    )
    session.add(campaign)
    await session.flush()

    # Create outreach targets from expansion plans
    for plan in plans:
        target = OutreachTarget(
            campaign_id=campaign.id,
            restaurant_id=plan.target_restaurant_id,
            status="pending",
            priority=int(plan.priority_score),
        )
        session.add(target)
        plan.status = "in_progress"

    await session.flush()

    await _record_event(session, cluster_id, "campaign_launched", {
        "campaign_id": str(campaign.id),
        "campaign_type": campaign_type,
        "target_count": len(plans),
    })

    logger.info("campaign_launched", cluster=str(cluster_id), campaign=str(campaign.id), targets=len(plans))
    return campaign


async def recalculate_cluster(
    session: AsyncSession,
    cluster_id: UUID,
) -> MerchantCluster:
    """Recalculate cluster scores and stats (NIF-158)."""
    cluster = await session.get(MerchantCluster, cluster_id)
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found")

    # Recalculate member count (only anchor + member, not prospects)
    count_result = await session.execute(
        select(func.count(ClusterMember.id)).where(
            and_(
                ClusterMember.cluster_id == cluster_id,
                ClusterMember.role.in_(["anchor", "member"]),
            )
        )
    )
    cluster.restaurant_count = count_result.scalar() or 0

    # Recalculate average ICP score from current ICP scores
    avg_result = await session.execute(
        select(func.avg(ICPScore.total_icp_score))
        .select_from(ClusterMember)
        .join(ICPScore, ICPScore.restaurant_id == ClusterMember.restaurant_id)
        .where(
            and_(
                ClusterMember.cluster_id == cluster_id,
                ClusterMember.role.in_(["anchor", "member"]),
            )
        )
    )
    cluster.avg_icp_score = round(float(avg_result.scalar() or 0.0), 4)

    # Update member icp_score_at_join with current scores
    members_result = await session.execute(
        select(ClusterMember, ICPScore.total_icp_score)
        .outerjoin(ICPScore, ICPScore.restaurant_id == ClusterMember.restaurant_id)
        .where(ClusterMember.cluster_id == cluster_id)
    )
    for member, current_score in members_result.all():
        member.icp_score_at_join = current_score or 0.0

    # Recalculate flywheel
    flywheel = await estimate_flywheel(session, cluster_id)

    # Update status based on metrics
    if cluster.restaurant_count >= 10 and cluster.flywheel_score >= 60.0:
        cluster.status = "mature"
    elif cluster.restaurant_count >= 5:
        cluster.status = "active"

    await session.flush()

    await _record_event(session, cluster_id, "recalculated", {
        "restaurant_count": cluster.restaurant_count,
        "avg_icp_score": cluster.avg_icp_score,
        "flywheel_score": cluster.flywheel_score,
    })

    logger.info("cluster_recalculated", cluster=str(cluster_id), score=cluster.avg_icp_score)
    return cluster


async def record_feedback(
    session: AsyncSession,
    cluster_id: UUID,
    feedback_type: str,
    details: dict | None = None,
    submitted_by: str = "system",
) -> ClusterFeedback:
    """Record feedback for a cluster (NIF-159)."""
    cluster = await session.get(MerchantCluster, cluster_id)
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found")

    feedback = ClusterFeedback(
        cluster_id=cluster_id,
        feedback_type=feedback_type,
        details=details or {},
        submitted_by=submitted_by,
    )
    session.add(feedback)
    await session.flush()

    logger.info("feedback_recorded", cluster=str(cluster_id), type=feedback_type)
    return feedback


async def get_cluster_history(
    session: AsyncSession,
    cluster_id: UUID,
) -> list[ClusterHistory]:
    """Return event history for a cluster (NIF-158)."""
    cluster = await session.get(MerchantCluster, cluster_id)
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found")

    result = await session.execute(
        select(ClusterHistory)
        .where(ClusterHistory.cluster_id == cluster_id)
        .order_by(ClusterHistory.created_at.desc())
    )
    return list(result.scalars().all())


async def list_clusters(
    session: AsyncSession,
    status: str | None = None,
    limit: int = 50,
) -> list[MerchantCluster]:
    """List clusters with optional status filter."""
    query = select(MerchantCluster)
    if status:
        query = query.where(MerchantCluster.status == status)
    query = query.order_by(MerchantCluster.flywheel_score.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_cluster_detail(
    session: AsyncSession,
    cluster_id: UUID,
) -> MerchantCluster | None:
    """Get cluster with members loaded."""
    result = await session.execute(
        select(MerchantCluster)
        .options(selectinload(MerchantCluster.members))
        .where(MerchantCluster.id == cluster_id)
    )
    return result.scalar_one_or_none()
