"""Celery tasks for ICP scoring."""

from src.tasks.celery_app import celery_app
from src.tasks.crawl_tasks import run_async
from src.utils.logging import get_logger

logger = get_logger("tasks.score")


@celery_app.task(name="src.tasks.score_tasks.score_restaurants")
def score_restaurants(restaurant_ids: list[str] | None = None):
    """Compute ICP scores for restaurants."""

    async def _score():
        from datetime import datetime, timezone
        from src.db.session import async_session
        from src.db.models import Restaurant, SourceRecord, ICPScore
        from src.scoring.icp_scorer import icp_scorer
        from src.scoring.geo_density import compute_density_scores
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert

        async with async_session() as session:
            query = select(Restaurant)
            if restaurant_ids:
                query = query.where(Restaurant.id.in_(restaurant_ids))
            result = await session.execute(query)
            restaurants = result.scalars().all()

            if not restaurants:
                return {"scored": 0}

            rest_ids = [r.id for r in restaurants]
            sr_result = await session.execute(
                select(SourceRecord).where(SourceRecord.restaurant_id.in_(rest_ids))
            )
            all_records = sr_result.scalars().all()

            sr_map = {}
            for sr in all_records:
                rid = str(sr.restaurant_id)
                sr_map.setdefault(rid, []).append({
                    "source": sr.source,
                    "source_url": sr.source_url,
                    "raw_data": sr.raw_data,
                    "extracted_data": sr.extracted_data,
                })

            rest_dicts = [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "address": r.address,
                    "lat": r.lat,
                    "lng": r.lng,
                    "review_count": 0,
                    "rating": 0.0,
                }
                for r in restaurants
            ]

            density_scores = compute_density_scores(rest_dicts)
            scores = icp_scorer.score_batch(rest_dicts, sr_map, density_scores)

            for score in scores:
                stmt = insert(ICPScore).values(
                    restaurant_id=score["restaurant_id"],
                    is_independent=score["is_independent"],
                    has_delivery=score["has_delivery"],
                    delivery_platforms=score["delivery_platforms"],
                    has_pos=score["has_pos"],
                    pos_provider=score["pos_provider"],
                    geo_density_score=score["geo_density_score"],
                    review_volume=score["review_volume"],
                    rating_avg=score["rating_avg"],
                    total_icp_score=score["total_icp_score"],
                    fit_label=score["fit_label"],
                    scoring_version=score["scoring_version"],
                    scored_at=datetime.now(timezone.utc),
                ).on_conflict_do_update(
                    index_elements=["restaurant_id"],
                    set_={
                        "is_independent": score["is_independent"],
                        "has_delivery": score["has_delivery"],
                        "delivery_platforms": score["delivery_platforms"],
                        "has_pos": score["has_pos"],
                        "pos_provider": score["pos_provider"],
                        "geo_density_score": score["geo_density_score"],
                        "review_volume": score["review_volume"],
                        "rating_avg": score["rating_avg"],
                        "total_icp_score": score["total_icp_score"],
                        "fit_label": score["fit_label"],
                        "scoring_version": score["scoring_version"],
                        "scored_at": datetime.now(timezone.utc),
                    },
                )
                await session.execute(stmt)

            await session.commit()
            logger.info("scoring_complete", scored=len(scores))
            return {"scored": len(scores)}

    return run_async(_score())


@celery_app.task(name="src.tasks.score_tasks.rescore_all")
def rescore_all():
    """Re-score all restaurants (weekly job)."""
    logger.info("full_rescore_triggered")
    return score_restaurants(restaurant_ids=None)
