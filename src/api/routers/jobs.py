"""Crawl job management endpoints."""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_api_key
from src.db.session import get_session
from src.db.models import CrawlJob
from src.api.schemas import CrawlJobCreate, CrawlJobResponse
from src.services.cleanup import CleanupService
from src.tasks.crawl_tasks import crawl_source
from src.tasks.extract_tasks import extract_records
from src.tasks.score_tasks import score_restaurants

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=CrawlJobResponse, status_code=201)
async def create_crawl_job(
    job_in: CrawlJobCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a new crawl job and dispatch it to the worker."""
    job = CrawlJob(
        source=job_in.source,
        query=job_in.query,
        location=job_in.location,
        status="pending",
    )
    session.add(job)
    await session.flush()

    # Dispatch celery task chain: crawl -> extract -> score
    from celery import chain
    pipeline = chain(
        crawl_source.s(job_in.source, job_in.query, job_in.location, str(job.id)),
        extract_records.si(),
        score_restaurants.si(),
    )
    pipeline.apply_async()

    await session.commit()
    return job


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
