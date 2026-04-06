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

from src.db.models import Lead, CrawlJob, Restaurant, ICPScore
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


# ── Restaurants ─────────────────────────────────────────────────


@router.get("/restaurants", response_class=HTMLResponse)
async def restaurants_dashboard(
    request: Request,
    city: str = Query(""),
    state: str = Query(""),
    fit_label: str = Query(""),
    min_score: str = Query(""),
    q: str = Query(""),
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
):
    """Restaurant database with ICP leaderboard."""
    from sqlalchemy.orm import joinedload

    filters = {"city": city, "state": state, "fit_label": fit_label, "min_score": min_score, "q": q}

    query = (
        select(Restaurant)
        .options(joinedload(Restaurant.icp_score))
        .outerjoin(ICPScore)
        .order_by(ICPScore.total_icp_score.desc().nullslast())
    )
    count_query = select(func.count(Restaurant.id))

    if city:
        query = query.where(Restaurant.city.ilike(f"%{city}%"))
        count_query = count_query.where(Restaurant.city.ilike(f"%{city}%"))
    if state:
        query = query.where(Restaurant.state == state.upper())
        count_query = count_query.where(Restaurant.state == state.upper())
    if fit_label:
        query = query.where(ICPScore.fit_label == fit_label)
        count_query = count_query.join(ICPScore).where(ICPScore.fit_label == fit_label)
    if min_score:
        try:
            ms = float(min_score)
            query = query.where(ICPScore.total_icp_score >= ms)
            count_query = count_query.join(ICPScore).where(ICPScore.total_icp_score >= ms)
        except ValueError:
            pass
    if q:
        query = query.where(Restaurant.name.ilike(f"%{q}%"))
        count_query = count_query.where(Restaurant.name.ilike(f"%{q}%"))

    total = await session.scalar(count_query) or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE))

    query = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    result = await session.execute(query)
    restaurants = result.unique().scalars().all()

    # Stats
    total_all = await session.scalar(select(func.count(Restaurant.id))) or 0
    independent = await session.scalar(
        select(func.count(ICPScore.id)).where(ICPScore.is_independent == True)
    ) or 0
    has_delivery = await session.scalar(
        select(func.count(ICPScore.id)).where(ICPScore.has_delivery == True)
    ) or 0
    has_pos = await session.scalar(
        select(func.count(ICPScore.id)).where(ICPScore.has_pos == True)
    ) or 0
    avg_score = await session.scalar(
        select(func.avg(ICPScore.total_icp_score))
    )

    stats = {
        "total": total_all,
        "independent": independent,
        "has_delivery": has_delivery,
        "has_pos": has_pos,
        "avg_score": float(avg_score) if avg_score else None,
    }

    html = templates.get_template("restaurants.html").render(
        restaurants=restaurants,
        stats=stats,
        filters=filters,
        page=page,
        total_pages=total_pages,
        active_tab="restaurants",
    )
    return HTMLResponse(html)


# ── Analytics ───────────────────────────────────────────────────


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Analytics dashboard with charts."""
    from collections import namedtuple

    Row = namedtuple("Row", ["label", "count"])

    # Overview stats
    total_leads = await session.scalar(select(func.count(Lead.id))) or 0
    total_restaurants = await session.scalar(select(func.count(Restaurant.id))) or 0
    total_jobs = await session.scalar(select(func.count(CrawlJob.id))) or 0
    won_leads = await session.scalar(
        select(func.count(Lead.id)).where(Lead.status.in_(["pilot", "won"]))
    ) or 0
    conversion_rate = (won_leads / total_leads * 100) if total_leads > 0 else 0

    overview = {
        "total_leads": total_leads,
        "total_restaurants": total_restaurants,
        "total_jobs": total_jobs,
        "conversion_rate": conversion_rate,
    }

    # Leads by source
    source_rows = await session.execute(
        select(Lead.source, func.count(Lead.id).label("cnt"))
        .group_by(Lead.source)
        .order_by(func.count(Lead.id).desc())
    )
    by_source = [{"source": r[0].replace("_", " ").title(), "count": r[1]} for r in source_rows.all()]

    # Leads by ICP fit
    fit_rows = await session.execute(
        select(Lead.icp_fit_label, func.count(Lead.id).label("cnt"))
        .group_by(Lead.icp_fit_label)
        .order_by(func.count(Lead.id).desc())
    )
    by_fit = [{"fit": (r[0] or "Unscored").title(), "count": r[1]} for r in fit_rows.all()]

    # Leads by status (pipeline)
    status_order = ["new", "contacted", "demo_scheduled", "pilot", "won", "lost"]
    status_rows = await session.execute(
        select(Lead.status, func.count(Lead.id).label("cnt"))
        .group_by(Lead.status)
    )
    status_map = {r[0]: r[1] for r in status_rows.all()}
    by_status = [{"status": s.replace("_", " ").title(), "count": status_map.get(s, 0)} for s in status_order]

    # Leads over time (last 30 days)
    from sqlalchemy import cast, Date
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    time_rows = await session.execute(
        select(
            cast(Lead.created_at, Date).label("day"),
            func.count(Lead.id).label("cnt"),
        )
        .where(Lead.created_at >= thirty_days_ago)
        .group_by("day")
        .order_by("day")
    )
    over_time = [{"date": str(r[0]), "count": r[1]} for r in time_rows.all()]

    # Fill gaps in time series
    if over_time:
        date_set = {d["date"] for d in over_time}
        all_dates = []
        for i in range(31):
            d = (thirty_days_ago + timedelta(days=i)).strftime("%Y-%m-%d")
            count = next((x["count"] for x in over_time if x["date"] == d), 0)
            all_dates.append({"date": d[5:], "count": count})  # MM-DD format
        over_time = all_dates

    # Top cities by lead count (from matched restaurants)
    city_rows = await session.execute(
        select(Restaurant.city, func.count(Lead.id).label("cnt"))
        .join(Lead, Lead.restaurant_id == Restaurant.id)
        .where(Restaurant.city.isnot(None))
        .group_by(Restaurant.city)
        .order_by(func.count(Lead.id).desc())
        .limit(10)
    )
    top_cities = [{"city": r[0], "count": r[1]} for r in city_rows.all()]

    html = templates.get_template("analytics.html").render(
        overview=overview,
        by_source=by_source,
        by_fit=by_fit,
        by_status=by_status,
        over_time=over_time,
        top_cities=top_cities,
        active_tab="analytics",
    )
    return HTMLResponse(html)
