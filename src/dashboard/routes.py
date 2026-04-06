"""Dashboard routes — server-rendered HTML via Jinja2 + HTMX."""

import csv
import io
import math
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Lead, CrawlJob
from src.db.session import get_session

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

templates = Environment(
    loader=FileSystemLoader("src/dashboard/templates"),
    autoescape=select_autoescape(["html"]),
)

PAGE_SIZE = 25


async def _get_stats(session: AsyncSession) -> dict:
    """Compute lead stats for the dashboard header."""
    total = await session.scalar(select(func.count(Lead.id)))
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    this_week = await session.scalar(
        select(func.count(Lead.id)).where(Lead.created_at >= week_ago)
    )
    avg_icp = await session.scalar(
        select(func.avg(Lead.icp_total_score)).where(Lead.icp_total_score.isnot(None))
    )
    excellent = await session.scalar(
        select(func.count(Lead.id)).where(Lead.icp_fit_label == "excellent")
    )
    return {
        "total": total or 0,
        "this_week": this_week or 0,
        "avg_icp_score": float(avg_icp) if avg_icp else None,
        "excellent_count": excellent or 0,
    }


@router.get("", response_class=HTMLResponse)
async def leads_dashboard(
    request: Request,
    status: str = Query(""),
    source: str = Query(""),
    icp_fit_label: str = Query(""),
    q: str = Query(""),
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
):
    """Main leads dashboard with filters and pagination."""
    filters = {"status": status, "source": source, "icp_fit_label": icp_fit_label, "q": q}

    query = select(Lead).order_by(Lead.created_at.desc())
    count_query = select(func.count(Lead.id))

    if status:
        query = query.where(Lead.status == status)
        count_query = count_query.where(Lead.status == status)
    if source:
        query = query.where(Lead.source == source)
        count_query = count_query.where(Lead.source == source)
    if icp_fit_label:
        query = query.where(Lead.icp_fit_label == icp_fit_label)
        count_query = count_query.where(Lead.icp_fit_label == icp_fit_label)
    if q:
        q_filter = Lead.email.ilike(f"%{q}%") | Lead.company.ilike(f"%{q}%")
        query = query.where(q_filter)
        count_query = count_query.where(q_filter)

    total = await session.scalar(count_query) or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE))

    query = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    result = await session.execute(query)
    leads = result.scalars().all()

    stats = await _get_stats(session)

    html = templates.get_template("leads.html").render(
        leads=leads,
        stats=stats,
        filters=filters,
        page=page,
        total_pages=total_pages,
        active_tab="leads",
    )
    return HTMLResponse(html)


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
async def lead_detail(
    lead_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Lead detail page with ICP score breakdown."""
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        return HTMLResponse("<h1>Lead not found</h1>", status_code=404)

    html = templates.get_template("lead_detail.html").render(
        lead=lead,
        active_tab="leads",
    )
    return HTMLResponse(html)


@router.patch("/leads/{lead_id}/status")
async def update_lead_status(
    lead_id: UUID,
    status: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    """HTMX endpoint to update lead status inline."""
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        return HTMLResponse("Not found", status_code=404)
    lead.status = status
    return HTMLResponse("", status_code=200)


@router.get("/export")
async def export_leads_csv(
    status: str = Query(""),
    source: str = Query(""),
    icp_fit_label: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """Export filtered leads as CSV download."""
    query = select(Lead).order_by(Lead.created_at.desc())
    if status:
        query = query.where(Lead.status == status)
    if source:
        query = query.where(Lead.source == source)
    if icp_fit_label:
        query = query.where(Lead.icp_fit_label == icp_fit_label)

    result = await session.execute(query)
    leads = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "First Name", "Last Name", "Email", "Company", "Business Type",
        "Locations", "Interest", "Source", "Status", "ICP Score", "ICP Fit",
        "Matched Restaurant", "Match Confidence", "Created At",
    ])
    for lead in leads:
        writer.writerow([
            lead.first_name, lead.last_name, lead.email, lead.company or "",
            lead.business_type or "", lead.locations or "", lead.interest or "",
            lead.source, lead.status, lead.icp_total_score or "",
            lead.icp_fit_label or "", lead.matched_restaurant_name or "",
            lead.match_confidence or "", lead.created_at.isoformat(),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads-export.csv"},
    )


# ── Crawl Jobs ──────────────────────────────────────────────────


async def _get_job_stats(session: AsyncSession) -> dict:
    """Compute crawl job stats."""
    total = await session.scalar(select(func.count(CrawlJob.id))) or 0
    running = await session.scalar(
        select(func.count(CrawlJob.id)).where(CrawlJob.status.in_(["pending", "running"]))
    ) or 0
    completed = await session.scalar(
        select(func.count(CrawlJob.id)).where(CrawlJob.status == "completed")
    ) or 0
    failed = await session.scalar(
        select(func.count(CrawlJob.id)).where(CrawlJob.status == "failed")
    ) or 0
    total_items = await session.scalar(
        select(func.sum(CrawlJob.total_items))
    ) or 0
    return {
        "total": total,
        "running": running,
        "completed": completed,
        "failed": failed,
        "total_items": total_items,
    }


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_dashboard(
    request: Request,
    message: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """Crawl jobs dashboard with job list and create form."""
    result = await session.execute(
        select(CrawlJob).order_by(CrawlJob.created_at.desc()).limit(50)
    )
    jobs = result.scalars().all()
    stats = await _get_job_stats(session)

    html = templates.get_template("jobs.html").render(
        jobs=jobs,
        stats=stats,
        message=message,
        active_tab="jobs",
    )
    return HTMLResponse(html)


@router.get("/jobs/list", response_class=HTMLResponse)
async def jobs_table_partial(
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial — just the jobs table for auto-refresh."""
    result = await session.execute(
        select(CrawlJob).order_by(CrawlJob.created_at.desc()).limit(50)
    )
    jobs = result.scalars().all()
    html = templates.get_template("jobs_table.html").render(jobs=jobs)
    return HTMLResponse(html)


@router.post("/jobs")
async def create_job_from_dashboard(
    source: str = Form(...),
    location: str = Form(...),
    query: str = Form("restaurants"),
    session: AsyncSession = Depends(get_session),
):
    """Create a crawl job from the dashboard form."""
    job = CrawlJob(
        source=source,
        query=query,
        location=location,
        status="pending",
    )
    session.add(job)
    await session.flush()

    # Dispatch celery pipeline
    try:
        from celery import chain
        from src.tasks.crawl_tasks import crawl_source
        from src.tasks.extract_tasks import extract_records
        from src.tasks.score_tasks import score_restaurants

        pipeline = chain(
            crawl_source.s(source, query, location, str(job.id)),
            extract_records.si(),
            score_restaurants.si(),
        )
        pipeline.apply_async()
    except Exception:
        # Celery not available (local dev without Redis)
        pass

    from fastapi.responses import RedirectResponse
    return RedirectResponse(
        url=f"/dashboard/jobs?message=Crawl job started: {source} in {location}",
        status_code=303,
    )
