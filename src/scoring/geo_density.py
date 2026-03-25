"""Geographic density scoring using HDBSCAN clustering."""

import numpy as np
from typing import Any

from src.utils.logging import get_logger

logger = get_logger("scoring.geo_density")

EARTH_RADIUS_KM = 6371.0


def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate haversine distance between two points in km."""
    lat1, lng1, lat2, lng2 = map(np.radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def compute_density_scores(
    restaurants: list[dict[str, Any]],
    radius_km: float = 0.5,
    min_cluster_size: int = 5,
) -> dict[str, float]:
    """Compute geo-density scores for restaurants using HDBSCAN clustering.

    Args:
        restaurants: List of dicts with 'id', 'lat', 'lng' keys.
        radius_km: Radius for neighbor counting.
        min_cluster_size: Minimum cluster size for HDBSCAN.

    Returns:
        Dict mapping restaurant ID to density score (0.0 - 1.0).
    """
    if not restaurants:
        return {}

    valid = [r for r in restaurants if r.get("lat") is not None and r.get("lng") is not None]
    if len(valid) < min_cluster_size:
        logger.warning("too_few_restaurants_for_clustering", count=len(valid))
        return {str(r["id"]): 0.5 for r in valid}

    coords = np.array([[r["lat"], r["lng"]] for r in valid])
    ids = [r["id"] for r in valid]

    # Neighbor counting within radius
    density_counts = []
    for i, (lat, lng) in enumerate(coords):
        neighbors = 0
        for j, (lat2, lng2) in enumerate(coords):
            if i != j:
                dist = haversine_distance(lat, lng, lat2, lng2)
                if dist <= radius_km:
                    neighbors += 1
        density_counts.append(neighbors)

    # HDBSCAN clustering for additional signal
    try:
        from hdbscan import HDBSCAN

        coords_rad = np.radians(coords)
        clusterer = HDBSCAN(
            min_cluster_size=min_cluster_size,
            metric="haversine",
            min_samples=2,
        )
        labels = clusterer.fit_predict(coords_rad)
        cluster_bonus = [0.1 if label >= 0 else 0.0 for label in labels]
    except ImportError:
        logger.warning("hdbscan_not_available_using_density_only")
        cluster_bonus = [0.0] * len(valid)

    # Normalize density counts to 0-1
    max_count = max(density_counts) if max(density_counts) > 0 else 1
    scores = {}
    for i, rid in enumerate(ids):
        base_score = density_counts[i] / max_count
        score = min(base_score + cluster_bonus[i], 1.0)
        scores[str(rid)] = round(score, 4)

    logger.info("density_scores_computed", total=len(scores), avg=round(np.mean(list(scores.values())), 4))
    return scores


def get_neighborhood_stats(restaurants: list[dict], radius_km: float = 1.0) -> dict:
    """Get aggregate stats about restaurant density in an area."""
    valid = [r for r in restaurants if r.get("lat") and r.get("lng")]
    if not valid:
        return {"total": 0, "avg_density": 0.0}

    scores = compute_density_scores(valid, radius_km=radius_km)
    score_values = list(scores.values())

    return {
        "total": len(valid),
        "avg_density": round(np.mean(score_values), 4) if score_values else 0.0,
        "max_density": round(max(score_values), 4) if score_values else 0.0,
        "min_density": round(min(score_values), 4) if score_values else 0.0,
        "dense_count": sum(1 for s in score_values if s >= 0.7),
    }
