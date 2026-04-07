"""Dashboard routes — server-rendered HTML via Jinja2 + HTMX."""

import csv
import io
import math
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import Lead, CrawlJob, Restaurant, ICPScore, AuditLog
from src.db.session import get_session
from src.services.cleanup import CleanupService
from src.utils.geo import haversine_miles, bounding_box

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

templates = Environment(
    loader=FileSystemLoader("src/dashboard/templates"),
    autoescape=select_autoescape(["html"]),
)

PAGE_SIZE = 25


def _require_login(request: Request) -> bool:
    """Check if the user is logged in. Returns True if authenticated."""
    if not settings.dashboard_password:
        return True  # No password set — open access (dev mode)
    return request.session.get("authenticated") is True


# ── Login / Logout ──────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = Query("")):
    """Show login form."""
    if _require_login(request):
        return RedirectResponse(url="/dashboard", status_code=303)
    html = templates.get_template("login.html").render(error=error)
    return HTMLResponse(html)


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Validate credentials and create session."""
    if (
        secrets.compare_digest(username, settings.dashboard_username)
        and secrets.compare_digest(password, settings.dashboard_password)
    ):
        request.session["authenticated"] = True
        request.session["username"] = username
        return RedirectResponse(url="/dashboard", status_code=303)

    html = templates.get_template("login.html").render(error="Invalid username or password")
    return HTMLResponse(html, status_code=401)


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/dashboard/login", status_code=303)


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
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)
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
    request: Request,
    lead_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Lead detail page with ICP score breakdown."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        return HTMLResponse("<h1>Lead not found</h1>", status_code=404)

    # Fetch audit logs for this lead
    audit_result = await session.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == "lead")
        .where(AuditLog.details["lead_id"].astext == str(lead_id))
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    )
    audit_logs = audit_result.scalars().all()

    html = templates.get_template("lead_detail.html").render(
        lead=lead,
        audit_logs=audit_logs,
        active_tab="leads",
    )
    return HTMLResponse(html)


@router.patch("/leads/{lead_id}/status")
async def update_lead_status(
    request: Request,
    lead_id: UUID,
    status: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    """HTMX endpoint to update lead status inline."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        return HTMLResponse("Not found", status_code=404)
    old_status = lead.status
    lead.status = status

    # Write audit log for status change
    if old_status != status:
        audit = AuditLog(
            action="lead_status_changed",
            entity_type="lead",
            details={"lead_id": str(lead_id), "old_status": old_status, "new_status": status, "email": lead.email},
            performed_by="dashboard",
        )
        session.add(audit)

    return HTMLResponse("", status_code=200)


@router.get("/export")
async def export_leads_csv(
    request: Request,
    status: str = Query(""),
    source: str = Query(""),
    icp_fit_label: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """Export filtered leads as CSV download."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)
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
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)
    result = await session.execute(
        select(CrawlJob).order_by(CrawlJob.created_at.desc()).limit(50)
    )
    jobs = result.scalars().all()
    stats = await _get_job_stats(session)

    html = templates.get_template("jobs.html").render(
        jobs=jobs,
        stats=stats,
        message=message,
        retention_days=settings.crawl_job_retention_days,
        active_tab="jobs",
    )
    return HTMLResponse(html)


@router.get("/jobs/list", response_class=HTMLResponse)
async def jobs_table_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial — just the jobs table for auto-refresh."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)
    result = await session.execute(
        select(CrawlJob).order_by(CrawlJob.created_at.desc()).limit(50)
    )
    jobs = result.scalars().all()
    html = templates.get_template("jobs_table.html").render(jobs=jobs)
    return HTMLResponse(html)


@router.post("/jobs")
async def create_job_from_dashboard(
    request: Request,
    source: str = Form(...),
    location: str = Form(...),
    query: str = Form("restaurants"),
    session: AsyncSession = Depends(get_session),
):
    """Create a crawl job from the dashboard form. Runs synchronously."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)
    job = CrawlJob(
        source=source,
        query=query,
        location=location,
        status="pending",
    )
    session.add(job)
    await session.flush()
    job_id = str(job.id)
    await session.commit()

    if settings.use_celery:
        try:
            from celery import chain
            from src.tasks.crawl_tasks import crawl_source
            from src.tasks.extract_tasks import extract_records
            from src.tasks.score_tasks import score_restaurants

            pipeline = chain(
                crawl_source.s(source, query, location, job_id),
                extract_records.si(),
                score_restaurants.si(),
            )
            pipeline.apply_async()
        except Exception:
            pass
    else:
        from src.api.routers.jobs import _run_crawl_inline
        await _run_crawl_inline(source, query, location, job_id)

    # Re-fetch job to get final status
    from src.db.session import async_session as async_session_factory
    async with async_session_factory() as fresh:
        result = await fresh.execute(select(CrawlJob).where(CrawlJob.id == job.id))
        final_job = result.scalar_one_or_none()
        items = final_job.total_items if final_job else 0
        status = final_job.status if final_job else "unknown"

    return RedirectResponse(
        url=f"/dashboard/jobs?message=Crawl completed: {source} in {location} — {items} restaurants found ({status})",
        status_code=303,
    )


@router.post("/jobs/cleanup")
async def cleanup_jobs_from_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Run cleanup from the dashboard."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)
    service = CleanupService(session)
    result = await service.run_full_cleanup(performed_by="dashboard")
    msg = (
        f"Cleanup complete: {result['jobs_deleted']} jobs deleted, "
        f"{result['stale_marked']} stale marked, "
        f"{result['orphans_cleaned']} orphans cleaned "
        f"({result['elapsed_ms']}ms)"
    )
    return RedirectResponse(
        url=f"/dashboard/jobs?message={msg}",
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
    message: str = Query(""),
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
):
    """Restaurant database with ICP leaderboard."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)
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
        message=message,
        page=page,
        total_pages=total_pages,
        active_tab="restaurants",
    )
    return HTMLResponse(html)


@router.post("/restaurants/rescore")
async def rescore_from_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Rescore all restaurants from the dashboard."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.scoring.icp_scorer import icp_scorer
    from src.scoring.geo_density import compute_density_scores
    from src.db.models import SourceRecord as SR
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    result = await session.execute(select(Restaurant))
    restaurants = result.scalars().all()

    rest_ids = [r.id for r in restaurants]
    sr_result = await session.execute(select(SR).where(SR.restaurant_id.in_(rest_ids)))
    all_records = sr_result.scalars().all()
    sr_map = {}
    for sr in all_records:
        sr_map.setdefault(str(sr.restaurant_id), []).append({
            "source": sr.source, "raw_data": sr.raw_data, "extracted_data": sr.extracted_data,
        })

    rest_dicts = [
        {"id": str(r.id), "name": r.name, "lat": r.lat, "lng": r.lng,
         "cuisine_type": r.cuisine_type or [], "review_count": r.review_count or 0,
         "rating": r.rating_avg or 0.0, "price_tier": r.price_tier}
        for r in restaurants
    ]

    density_scores = compute_density_scores(rest_dicts)
    scores = icp_scorer.score_batch(rest_dicts, sr_map, density_scores)

    from src.api.routers.jobs import _build_score_values
    for score in scores:
        values = _build_score_values(score)
        stmt = pg_insert(ICPScore).values(**values).on_conflict_do_update(
            index_elements=["restaurant_id"],
            set_={k: v for k, v in values.items() if k != "restaurant_id"},
        )
        await session.execute(stmt)
    await session.commit()

    return RedirectResponse(
        url=f"/dashboard/restaurants?message=Rescored {len(scores)} restaurants with v2 model",
        status_code=303,
    )


# ── Analytics ───────────────────────────────────────────────────


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Analytics dashboard with charts."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)
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


# ── Prospect Finder ─────────────────────────────────────────────


async def _find_prospects(
    session: AsyncSession,
    zip_code: str,
    radius: float,
    min_score: float | None,
    independent_only: bool,
    has_delivery: bool,
) -> list[dict]:
    """Find restaurants near a ZIP code with ICP scores and distance."""
    from sqlalchemy.orm import joinedload

    # Get centroid for ZIP
    centroid = await session.execute(
        select(
            func.avg(Restaurant.lat).label("lat"),
            func.avg(Restaurant.lng).label("lng"),
        ).where(
            Restaurant.zip_code == zip_code,
            Restaurant.lat.isnot(None),
            Restaurant.lng.isnot(None),
        )
    )
    row = centroid.one()
    if not row.lat or not row.lng:
        return []

    center_lat, center_lng = float(row.lat), float(row.lng)
    min_lat, max_lat, min_lng, max_lng = bounding_box(center_lat, center_lng, radius)

    query = (
        select(Restaurant)
        .options(joinedload(Restaurant.icp_score))
        .outerjoin(ICPScore)
        .where(
            Restaurant.lat.isnot(None),
            Restaurant.lng.isnot(None),
            Restaurant.lat.between(min_lat, max_lat),
            Restaurant.lng.between(min_lng, max_lng),
        )
    )

    if independent_only:
        query = query.where(Restaurant.is_chain == False)
    if has_delivery:
        query = query.where(ICPScore.has_delivery == True)
    if min_score is not None:
        query = query.where(ICPScore.total_icp_score >= min_score)

    result = await session.execute(query)
    candidates = result.unique().scalars().all()

    prospects = []
    for r in candidates:
        dist = haversine_miles(center_lat, center_lng, r.lat, r.lng)
        if dist <= radius:
            score = r.icp_score
            prospects.append({
                "id": r.id,
                "name": r.name,
                "address": r.address,
                "city": r.city,
                "state": r.state,
                "zip_code": r.zip_code,
                "phone": r.phone,
                "website": r.website,
                "cuisine_type": r.cuisine_type or [],
                "is_chain": r.is_chain,
                "is_independent": not r.is_chain if r.is_chain is not None else None,
                "distance": round(dist, 1),
                "icp_score": score.total_icp_score if score else None,
                "fit_label": score.fit_label if score else None,
                "has_delivery": score.has_delivery if score else None,
                "delivery_platforms": score.delivery_platforms or [] if score else [],
                "has_pos": score.has_pos if score else None,
                "pos_provider": score.pos_provider if score else None,
                "geo_density": score.geo_density_score if score else None,
                "volume_proxy": score.volume_proxy if score else None,
                "cuisine_fit": score.cuisine_fit if score else None,
                "price_tier": score.price_tier if score else None,
                "price_point_fit": score.price_point_fit if score else None,
                "engagement_recency": score.engagement_recency if score else None,
                "disqualifier_penalty": score.disqualifier_penalty if score else None,
            })

    # Sort by ICP score (desc), then distance (asc)
    prospects.sort(key=lambda p: (-(p["icp_score"] or 0), p["distance"]))
    return prospects


@router.get("/prospects", response_class=HTMLResponse)
async def prospect_finder(
    request: Request,
    zip_code: str = Query(""),
    radius: int = Query(5, ge=1, le=25),
    min_score: str = Query(""),
    independent_only: str = Query(""),
    has_delivery: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """Prospect Finder — ZIP code proximity search with ICP ranking."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    filters = {
        "zip_code": zip_code,
        "radius": radius,
        "min_score": min_score,
        "independent_only": independent_only == "1",
        "has_delivery": has_delivery == "1",
    }

    searched = bool(zip_code and len(zip_code) == 5)
    prospects = []
    stats = {}

    if searched:
        ms = None
        if min_score:
            try:
                ms = float(min_score)
            except ValueError:
                pass

        prospects = await _find_prospects(
            session, zip_code, float(radius), ms,
            filters["independent_only"], filters["has_delivery"],
        )

        scores = [p["icp_score"] for p in prospects if p["icp_score"] is not None]
        distances = [p["distance"] for p in prospects]
        stats = {
            "total": len(prospects),
            "excellent": sum(1 for p in prospects if p.get("fit_label") == "excellent"),
            "good": sum(1 for p in prospects if p.get("fit_label") == "good"),
            "avg_score": sum(scores) / len(scores) if scores else None,
            "avg_distance": sum(distances) / len(distances) if distances else None,
        }

    html = templates.get_template("prospects.html").render(
        prospects=prospects,
        stats=stats,
        filters=filters,
        searched=searched,
        active_tab="prospects",
    )
    return HTMLResponse(html)


@router.get("/prospects/export")
async def export_prospects_csv(
    request: Request,
    zip_code: str = Query(...),
    radius: int = Query(5),
    min_score: str = Query(""),
    independent_only: str = Query(""),
    has_delivery: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """Export prospect search results as CSV for outreach."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    ms = None
    if min_score:
        try:
            ms = float(min_score)
        except ValueError:
            pass

    prospects = await _find_prospects(
        session, zip_code, float(radius), ms,
        independent_only == "1", has_delivery == "1",
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name", "Address", "City", "State", "ZIP", "Phone", "Website",
        "Distance (mi)", "ICP Score", "ICP Fit", "Independent",
        "Delivery Platforms", "POS Provider", "Cuisines",
    ])
    for p in prospects:
        writer.writerow([
            p["name"], p["address"] or "", p["city"] or "", p["state"] or "",
            p["zip_code"] or "", p["phone"] or "", p["website"] or "",
            p["distance"], p["icp_score"] or "", p["fit_label"] or "",
            "Yes" if p["is_independent"] else "No" if p["is_independent"] is not None else "",
            ", ".join(p["delivery_platforms"]) if p["delivery_platforms"] else "",
            p["pos_provider"] or "", ", ".join(p["cuisine_type"]),
        ])

    output.seek(0)
    filename = f"prospects-{zip_code}-{radius}mi.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
