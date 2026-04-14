"""Crawl job management endpoints."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.config import settings
from src.db.session import get_session, async_session
from src.db.models import CrawlJob, Restaurant, SourceRecord
from src.api.schemas import CrawlJobCreate, CrawlJobResponse
from src.services.cleanup import CleanupService
from src.utils.logging import get_logger

logger = get_logger("jobs")

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _build_score_values(score: dict) -> dict:
    """Build values dict for ICPScore upsert with all v2 fields."""
    return {
        "restaurant_id": score["restaurant_id"],
        "is_independent": score["is_independent"],
        "has_delivery": score["has_delivery"],
        "delivery_platforms": score["delivery_platforms"],
        "delivery_platform_count": score.get("delivery_platform_count", 0),
        "has_pos": score["has_pos"],
        "pos_provider": score["pos_provider"],
        "geo_density_score": score["geo_density_score"],
        "review_volume": score["review_volume"],
        "rating_avg": score["rating_avg"],
        "volume_proxy": score.get("volume_proxy", 0.0),
        "cuisine_fit": score.get("cuisine_fit", 1.0),
        "price_tier": score.get("price_tier"),
        "price_point_fit": score.get("price_point_fit", 0.7),
        "engagement_recency": score.get("engagement_recency", 0.3),
        "disqualifier_penalty": score.get("disqualifier_penalty", 0.0),
        "total_icp_score": score["total_icp_score"],
        "fit_label": score["fit_label"],
        "scoring_version": score["scoring_version"],
        "scored_at": datetime.now(timezone.utc),
    }


async def _score_restaurants_inline(session: AsyncSession) -> int:
    """Score all unscored restaurants. Runs after crawl."""
    from src.scoring.icp_scorer import icp_scorer
    from src.scoring.geo_density import compute_density_scores
    from src.db.models import ICPScore, SourceRecord as SR
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    # Find restaurants without ICP scores
    scored_ids = select(ICPScore.restaurant_id)
    result = await session.execute(
        select(Restaurant).where(Restaurant.id.notin_(scored_ids))
    )
    unscored = result.scalars().all()
    if not unscored:
        return 0

    rest_ids = [r.id for r in unscored]
    sr_result = await session.execute(select(SR).where(SR.restaurant_id.in_(rest_ids)))
    all_records = sr_result.scalars().all()

    sr_map = {}
    for sr in all_records:
        rid = str(sr.restaurant_id)
        sr_map.setdefault(rid, []).append({
            "source": sr.source,
            "raw_data": sr.raw_data,
            "extracted_data": sr.extracted_data,
        })

    rest_dicts = [
        {"id": str(r.id), "name": r.name, "lat": r.lat, "lng": r.lng,
         "cuisine_type": r.cuisine_type or [], "review_count": r.review_count or 0,
         "rating": r.rating_avg or 0.0, "price_tier": r.price_tier}
        for r in unscored
    ]

    density_scores = compute_density_scores(rest_dicts)
    scores = icp_scorer.score_batch(rest_dicts, sr_map, density_scores)

    for score in scores:
        values = _build_score_values(score)
        stmt = pg_insert(ICPScore).values(**values).on_conflict_do_update(
            index_elements=["restaurant_id"],
            set_={k: v for k, v in values.items() if k != "restaurant_id"},
        )
        await session.execute(stmt)

    await session.commit()
    return len(scores)


async def _run_crawl_inline(source: str, query: str, location: str, job_id: str):
    """Run crawl pipeline inline (no Celery). Used when Redis/workers unavailable."""
    from src.tasks.crawl_tasks import _get_crawler

    crawler = _get_crawler(source)
    if not crawler:
        async with async_session() as session:
            job = await session.get(CrawlJob, job_id)
            if job:
                job.status = "failed"
                job.error_message = f"Unknown source: {source}"
                job.finished_at = datetime.now(timezone.utc)
                await session.commit()
        return

    async with async_session() as session:
        # Mark running
        job = await session.get(CrawlJob, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            await session.commit()

        try:
            results = await crawler.run(query, location)
            logger.info("inline_crawl_results", source=source, count=len(results))
            count = 0

            for record in results:
                name = record.get("name", "").strip()
                address = record.get("address", "").strip()
                if not name:
                    continue

                cuisine = record.get("cuisine_type") or []
                if not isinstance(cuisine, list):
                    cuisine = [cuisine] if cuisine else []
                if not cuisine and record.get("cuisine"):
                    c = record["cuisine"]
                    cuisine = [c] if c and c != "Restaurant" else []

                rating = record.get("rating")
                review_count = record.get("review_count", 0) or 0
                price_tier = record.get("price_tier")

                stmt = insert(Restaurant).values(
                    name=name,
                    address=address or None,
                    city=record.get("city"),
                    state=record.get("state"),
                    zip_code=record.get("zip_code"),
                    lat=record.get("lat"),
                    lng=record.get("lng"),
                    phone=record.get("phone"),
                    website=record.get("website"),
                    cuisine_type=cuisine,
                    rating_avg=rating,
                    review_count=review_count,
                    price_tier=price_tier,
                ).on_conflict_do_update(
                    constraint="uq_restaurant_name_address",
                    set_={
                        "lat": record.get("lat"),
                        "lng": record.get("lng"),
                        "phone": record.get("phone"),
                        "website": record.get("website"),
                        "cuisine_type": cuisine,
                        "rating_avg": rating,
                        "review_count": review_count,
                        "price_tier": price_tier,
                        "updated_at": datetime.now(timezone.utc),
                    },
                )
                await session.execute(stmt)
                await session.flush()

                rest = (await session.execute(
                    select(Restaurant).where(
                        Restaurant.name == name,
                        Restaurant.address == (address or None),
                    )
                )).scalar_one_or_none()

                if rest:
                    session.add(SourceRecord(
                        restaurant_id=rest.id,
                        source=record.get("source", source),
                        source_url=record.get("source_url"),
                        raw_data=record,
                        crawled_at=datetime.now(timezone.utc),
                    ))
                    count += 1

            await session.commit()

            # Step 2: Score the crawled restaurants
            scored = await _score_restaurants_inline(session)
            logger.info("inline_scoring_complete", scored=scored)

            # Mark completed
            job = await session.get(CrawlJob, job_id)
            if job:
                job.status = "completed"
                job.total_items = count
                job.finished_at = datetime.now(timezone.utc)
                await session.commit()

            logger.info("inline_crawl_complete", source=source, items=count, scored=scored, job_id=job_id)

        except Exception as e:
            logger.error("inline_crawl_failed", source=source, error=str(e), job_id=job_id)
            async with async_session() as err_session:
                job = await err_session.get(CrawlJob, job_id)
                if job:
                    job.status = "failed"
                    job.error_message = str(e)[:500]
                    job.finished_at = datetime.now(timezone.utc)
                    await err_session.commit()


@router.post("", response_model=CrawlJobResponse, status_code=201)
async def create_crawl_job(
    job_in: CrawlJobCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a crawl job and run it. Tries Celery first, falls back to synchronous inline."""
    job = CrawlJob(
        source=job_in.source,
        query=job_in.query,
        location=job_in.location,
        status="pending",
    )
    session.add(job)
    await session.flush()
    job_id = str(job.id)
    await session.commit()

    if job_in.source == "all":
        # Multi-source crawl: run google_maps, yelp, delivery sequentially
        total_items = 0
        async with async_session() as s:
            j = await s.get(CrawlJob, job.id)
            if j:
                j.status = "running"
                j.started_at = datetime.now(timezone.utc)
                await s.commit()

        for src in ["google_maps", "yelp", "delivery"]:
            try:
                await _run_crawl_inline(src, job_in.query, job_in.location, None)
                logger.info("multi_source_step_complete", source=src, location=job_in.location)
            except Exception as e:
                logger.warning("multi_source_step_failed", source=src, error=str(e))

        # Score after all sources complete
        async with async_session() as s:
            scored = await _score_restaurants_inline(s)
            j = await s.get(CrawlJob, job.id)
            if j:
                j.status = "completed"
                j.total_items = scored
                j.finished_at = datetime.now(timezone.utc)
                await s.commit()
        logger.info("multi_source_crawl_complete", location=job_in.location, scored=scored)
    elif settings.use_celery:
        try:
            from celery import chain
            from src.tasks.crawl_tasks import crawl_source
            from src.tasks.extract_tasks import extract_records
            from src.tasks.score_tasks import score_restaurants

            pipeline = chain(
                crawl_source.s(job_in.source, job_in.query, job_in.location, job_id),
                extract_records.si(),
                score_restaurants.si(),
            )
            pipeline.apply_async(link_error=score_restaurants.si())
            logger.info("crawl_dispatched_celery", job_id=job_id)
        except Exception as e:
            logger.error("celery_dispatch_failed", error=str(e), job_id=job_id)
    else:
        await _run_crawl_inline(job_in.source, job_in.query, job_in.location, job_id)
        logger.info("crawl_completed_inline", job_id=job_id)

    # Re-fetch to return final state
    async with async_session() as fresh_session:
        result = await fresh_session.execute(select(CrawlJob).where(CrawlJob.id == job.id))
        return result.scalar_one()


@router.get("", response_model=list[CrawlJobResponse])
async def list_jobs(
    status: str | None = None,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
):
    """List crawl jobs with optional status filter."""
    query = select(CrawlJob).order_by(CrawlJob.created_at.desc()).limit(limit)
    if status:
        query = query.where(CrawlJob.status == status)
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/{job_id}", response_model=CrawlJobResponse)
async def get_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get crawl job status."""
    job = await session.get(CrawlJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/enrich-websites", dependencies=[Depends(require_auth)])
async def enrich_websites(
    limit: int = Query(50, ge=1, le=200, description="Max restaurants to enrich"),
    session: AsyncSession = Depends(get_session),
):
    """Crawl restaurant websites to detect POS systems and chain indicators."""
    from src.services.website_enrichment import WebsiteEnrichmentService
    service = WebsiteEnrichmentService(session)
    result = await service.enrich_batch(limit=limit)
    return result


@router.delete("/cleanup", dependencies=[Depends(require_auth)])
async def cleanup_old_jobs(
    max_age_days: int = Query(None, ge=1, le=365, description="Override retention period in days"),
    session: AsyncSession = Depends(get_session),
):
    """Delete old completed/failed jobs, mark stale jobs, clean orphaned records."""
    service = CleanupService(session)
    result = await service.run_full_cleanup(
        max_age_days=max_age_days,
        performed_by="api",
    )
    return result
