"""Crawl job management endpoints."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_api_key
from src.db.session import get_session, async_session
from src.db.models import CrawlJob, Restaurant, SourceRecord
from src.api.schemas import CrawlJobCreate, CrawlJobResponse
from src.services.cleanup import CleanupService
from src.utils.logging import get_logger

logger = get_logger("jobs")

router = APIRouter(prefix="/jobs", tags=["jobs"])


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

                cuisine = record.get("cuisine_type", [])
                if not isinstance(cuisine, list):
                    cuisine = [record.get("cuisine")] if record.get("cuisine") else []

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
                ).on_conflict_do_update(
                    constraint="uq_restaurant_name_address",
                    set_={
                        "lat": record.get("lat"),
                        "lng": record.get("lng"),
                        "phone": record.get("phone"),
                        "website": record.get("website"),
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

            # Mark completed
            job = await session.get(CrawlJob, job_id)
            if job:
                job.status = "completed"
                job.total_items = count
                job.finished_at = datetime.now(timezone.utc)
                await session.commit()

            logger.info("inline_crawl_complete", source=source, items=count, job_id=job_id)

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

    # Try Celery pipeline first
    celery_dispatched = False
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
        pipeline.apply_async()
        celery_dispatched = True
        logger.info("crawl_dispatched_celery", job_id=job_id)
    except Exception as e:
        logger.warning("celery_unavailable_running_inline", error=str(e), job_id=job_id)

    # Fallback: run crawl inline (synchronous, blocks until done)
    if not celery_dispatched:
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


@router.delete("/cleanup", dependencies=[Depends(require_api_key)])
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
