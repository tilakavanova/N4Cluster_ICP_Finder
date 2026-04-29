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
from src.db.models import (
    Lead, CrawlJob, Restaurant, ICPScore, AuditLog,
    Neighborhood,
    OutreachCampaign, OutreachTarget, OutreachActivity, OutreachPerformance,
    RepQueueItem, RepQueueRanking,
    QualificationResult, QualificationExplanation,
    MerchantCluster, ClusterMember, ClusterExpansionPlan,
    FollowUpTask, LeadStageHistory, LeadAssignmentHistory, Account, Contact,
    ConversionFunnel, ConversionEvent,
    TrackerEvent,
)
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

    # NIF-207: Follow-up tasks
    tasks_result = await session.execute(
        select(FollowUpTask)
        .where(FollowUpTask.lead_id == lead_id)
        .order_by(FollowUpTask.created_at.desc())
    )
    tasks = tasks_result.scalars().all()

    # NIF-207: Stage history
    stage_history_result = await session.execute(
        select(LeadStageHistory)
        .where(LeadStageHistory.lead_id == lead_id)
        .order_by(LeadStageHistory.changed_at.desc())
    )
    stage_history = stage_history_result.scalars().all()

    # NIF-207: Assignment history
    assignment_history_result = await session.execute(
        select(LeadAssignmentHistory)
        .where(LeadAssignmentHistory.lead_id == lead_id)
        .order_by(LeadAssignmentHistory.changed_at.desc())
    )
    assignment_history = assignment_history_result.scalars().all()

    # NIF-207: Account and contact from relationships
    account = None
    contact = None
    if lead.account_id:
        account = await session.get(Account, lead.account_id)
    if lead.contact_id:
        contact = await session.get(Contact, lead.contact_id)

    html = templates.get_template("lead_detail.html").render(
        lead=lead,
        audit_logs=audit_logs,
        tasks=tasks,
        stage_history=stage_history,
        assignment_history=assignment_history,
        account=account,
        contact=contact,
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
    """Create a crawl job and run it in the background. Redirects immediately so the
    HTMX polling (every 5s) shows the job in running status."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    import asyncio

    job = CrawlJob(
        source=source,
        query=query,
        location=location,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    session.add(job)
    await session.flush()
    job_id = str(job.id)
    await session.commit()

    async def _run_crawl_background(job_id: str, source: str, query: str, location: str):
        """Run crawl in background task so the dashboard can show running status."""
        from src.api.routers.jobs import _run_crawl_inline, _score_restaurants_inline
        from src.db.session import async_session as async_session_factory

        try:
            if source == "all":
                for src_name in ["google_maps", "yelp", "delivery"]:
                    try:
                        await _run_crawl_inline(src_name, query, location, None)
                    except Exception:
                        pass
            else:
                await _run_crawl_inline(source, query, location, job_id)

            # Score and finalize
            async with async_session_factory() as s:
                scored = await _score_restaurants_inline(s)
                j = await s.get(CrawlJob, job.id)
                if j:
                    j.status = "completed"
                    j.total_items = scored
                    j.finished_at = datetime.now(timezone.utc)
                    await s.commit()
        except Exception as exc:
            async with async_session_factory() as s:
                j = await s.get(CrawlJob, job.id)
                if j:
                    j.status = "failed"
                    j.error_message = str(exc)[:500]
                    j.finished_at = datetime.now(timezone.utc)
                    await s.commit()

    # Launch background task and redirect immediately
    asyncio.create_task(_run_crawl_background(job_id, source, query, location))

    return RedirectResponse(
        url=f"/dashboard/jobs?message=Crawl started: {source} in {location} — watch the table for progress",
        status_code=303,
    )


@router.post("/jobs/deep-crawl")
async def deep_crawl_city(
    request: Request,
    city: str = Form(...),
    state: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    """Deep crawl a city by iterating through all its ZIP codes."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    # Create tracking job
    job = CrawlJob(
        source="google_maps",
        query=f"deep_crawl:{city}",
        location=f"{city}, {state}",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    session.add(job)
    await session.flush()
    job_id = str(job.id)
    await session.commit()

    from src.services.zipcode_crawl import ZipCodeCrawlService
    service = ZipCodeCrawlService(session)
    result = await service.crawl_city(city, state, job_id=job_id)

    # Score all new restaurants
    from src.api.routers.jobs import _score_restaurants_inline
    from src.db.session import async_session as async_session_factory
    async with async_session_factory() as s:
        scored = await _score_restaurants_inline(s)

    msg = (
        f"Deep crawl complete: {city}, {state} — "
        f"{result['zip_codes_processed']}/{result['zip_codes_total']} ZIPs, "
        f"{result['new_restaurants_found']} new restaurants found "
        f"(total: {result['restaurants_after']}), {scored} scored"
    )
    return RedirectResponse(url=f"/dashboard/jobs?message={msg}", status_code=303)


@router.post("/jobs/enrich-websites")
async def enrich_websites_from_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Run website enrichment for POS detection from dashboard."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)
    from src.services.website_enrichment import WebsiteEnrichmentService
    service = WebsiteEnrichmentService(session)
    result = await service.enrich_batch(limit=50)
    msg = (
        f"Website enrichment complete: {result['enriched']} sites crawled, "
        f"{result['pos_detected']} POS detected, "
        f"{result['chains_detected']} chains detected, "
        f"{result['errors']} errors"
    )
    return RedirectResponse(
        url=f"/dashboard/jobs?message={msg}",
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
    restaurants = []
    total_pages = 1

    try:
        data_query = (
            select(Restaurant)
            .options(joinedload(Restaurant.icp_score))
            .outerjoin(ICPScore)
            .order_by(ICPScore.total_icp_score.desc().nullslast())
        )
        count_query = select(func.count(Restaurant.id))

        if city:
            data_query = data_query.where(Restaurant.city.ilike(f"%{city}%"))
            count_query = count_query.where(Restaurant.city.ilike(f"%{city}%"))
        if state:
            data_query = data_query.where(Restaurant.state == state.upper())
            count_query = count_query.where(Restaurant.state == state.upper())
        if fit_label:
            data_query = data_query.where(ICPScore.fit_label == fit_label)
            count_query = count_query.join(ICPScore).where(ICPScore.fit_label == fit_label)
        if min_score:
            try:
                ms = float(min_score)
                data_query = data_query.where(ICPScore.total_icp_score >= ms)
                count_query = count_query.join(ICPScore).where(ICPScore.total_icp_score >= ms)
            except ValueError:
                pass
        if q:
            data_query = data_query.where(Restaurant.name.ilike(f"%{q}%"))
            count_query = count_query.where(Restaurant.name.ilike(f"%{q}%"))

        total = await session.scalar(count_query) or 0
        total_pages = max(1, math.ceil(total / PAGE_SIZE))

        data_query = data_query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        result = await session.execute(data_query)
        restaurants = result.unique().scalars().all()
    except Exception as exc:
        message = message or f"Error loading restaurants: {exc}"

    # Stats — filtered to match the current query (NIF-210 + NIF-275 fix)
    try:
        search_text = q  # preserve before any shadowing
        stat_base = select(Restaurant.id).outerjoin(ICPScore, ICPScore.restaurant_id == Restaurant.id)
        if city:
            stat_base = stat_base.where(Restaurant.city.ilike(f"%{city}%"))
        if state:
            stat_base = stat_base.where(Restaurant.state == state.upper())
        if fit_label:
            stat_base = stat_base.where(ICPScore.fit_label == fit_label)
        if min_score:
            try:
                stat_base = stat_base.where(ICPScore.total_icp_score >= float(min_score))
            except ValueError:
                pass
        if search_text:
            stat_base = stat_base.where(Restaurant.name.ilike(f"%{search_text}%"))

        filtered_ids = stat_base.subquery()

        total_all = await session.scalar(
            select(func.count()).select_from(filtered_ids)
        ) or 0
        independent = await session.scalar(
            select(func.count(Restaurant.id))
            .where(Restaurant.id.in_(select(filtered_ids.c.id)))
            .where(Restaurant.is_chain == False)  # noqa: E712
        ) or 0
        has_delivery = await session.scalar(
            select(func.count(ICPScore.id))
            .where(ICPScore.restaurant_id.in_(select(filtered_ids.c.id)))
            .where(ICPScore.has_delivery == True)  # noqa: E712
        ) or 0
        has_pos = await session.scalar(
            select(func.count(ICPScore.id))
            .where(ICPScore.restaurant_id.in_(select(filtered_ids.c.id)))
            .where(ICPScore.has_pos == True)  # noqa: E712
        ) or 0
        avg_score = await session.scalar(
            select(func.avg(ICPScore.total_icp_score))
            .where(ICPScore.restaurant_id.in_(select(filtered_ids.c.id)))
        )

        stats = {
            "total": total_all,
            "independent": independent,
            "has_delivery": has_delivery,
            "has_pos": has_pos,
            "avg_score": float(avg_score) if avg_score else None,
        }
    except Exception:
        stats = {"total": 0, "independent": 0, "has_delivery": 0, "has_pos": 0, "avg_score": None}

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

    # NIF-208: Conversion funnel — latest period
    funnel = None
    try:
        funnel_result = await session.execute(
            select(ConversionFunnel)
            .order_by(ConversionFunnel.last_calculated_at.desc())
            .limit(1)
        )
        funnel_row = funnel_result.scalar_one_or_none()
        if funnel_row:
            funnel = {
                "period": funnel_row.period,
                "discovered": funnel_row.discovered,
                "contacted": funnel_row.contacted,
                "demo_scheduled": funnel_row.demo_scheduled,
                "pilot_started": funnel_row.pilot_started,
                "converted": funnel_row.converted,
                "churned": funnel_row.churned,
                "conversion_rate": funnel_row.conversion_rate,
                "avg_days_to_convert": funnel_row.avg_days_to_convert,
            }
    except Exception:
        funnel = None

    # NIF-208: Top neighborhoods by opportunity score
    top_neighborhoods = []
    try:
        neighborhood_rows = await session.execute(
            select(Neighborhood)
            .order_by(Neighborhood.opportunity_score.desc())
            .limit(5)
        )
        top_neighborhoods = [
            {
                "zip_code": n.zip_code,
                "name": n.name,
                "opportunity_score": n.opportunity_score,
                "restaurant_count": n.restaurant_count,
            }
            for n in neighborhood_rows.scalars().all()
        ]
    except Exception:
        top_neighborhoods = []

    # NIF-208: Cluster stats by status
    cluster_stats = {}
    try:
        cluster_status_rows = await session.execute(
            select(MerchantCluster.status, func.count(MerchantCluster.id))
            .group_by(MerchantCluster.status)
        )
        cluster_stats = {r[0]: r[1] for r in cluster_status_rows.all()}
    except Exception:
        cluster_stats = {}

    html = templates.get_template("analytics.html").render(
        overview=overview,
        by_source=by_source,
        by_fit=by_fit,
        by_status=by_status,
        over_time=over_time,
        top_cities=top_cities,
        funnel=funnel,
        top_neighborhoods=top_neighborhoods,
        cluster_stats=cluster_stats,
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


# ── Neighborhoods (NIF-202) ───────────────────────────────────


@router.get("/neighborhoods", response_class=HTMLResponse)
async def neighborhoods_dashboard(
    request: Request,
    state: str = Query(""),
    city: str = Query(""),
    min_restaurants: int = Query(3, ge=1),
    page: int = Query(1, ge=1),
    message: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """Neighborhood ranking page with filters and pagination."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.neighborhoods import rank_neighborhoods

    filters = {"state": state, "city": city, "min_restaurants": min_restaurants}
    offset = (page - 1) * PAGE_SIZE

    try:
        neighborhoods = await rank_neighborhoods(
            session,
            state=state or None,
            city=city or None,
            min_restaurants=min_restaurants,
            limit=PAGE_SIZE,
            offset=offset,
        )
    except Exception as exc:
        neighborhoods = []
        message = message or f"Error loading neighborhoods: {exc}"

    # Total count for pagination
    count_query = select(func.count(Neighborhood.id)).where(
        Neighborhood.restaurant_count >= min_restaurants
    )
    if state:
        count_query = count_query.where(Neighborhood.state == state.upper())
    if city:
        count_query = count_query.where(Neighborhood.city.ilike(f"%{city}%"))
    total = await session.scalar(count_query) or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE))

    html = templates.get_template("neighborhoods.html").render(
        neighborhoods=neighborhoods,
        filters=filters,
        page=page,
        total_pages=total_pages,
        message=message,
        active_tab="neighborhoods",
    )
    return HTMLResponse(html)


@router.post("/neighborhoods/refresh-all")
async def refresh_all_neighborhoods_route(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Trigger a refresh of all neighborhood scores."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.neighborhoods import refresh_all_neighborhoods

    try:
        count = await refresh_all_neighborhoods(session)
        await session.commit()
        msg = f"Refreshed {count} neighborhoods"
    except Exception as exc:
        msg = f"Error refreshing neighborhoods: {exc}"

    return RedirectResponse(
        url=f"/dashboard/neighborhoods?message={msg}",
        status_code=303,
    )


@router.post("/neighborhoods/compare", response_class=HTMLResponse)
async def compare_neighborhoods_route(
    request: Request,
    zip_codes: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial — compare selected neighborhoods side by side."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    from src.services.neighborhoods import compare_neighborhoods

    zips = [z.strip() for z in zip_codes.split(",") if z.strip()]
    if not zips:
        return HTMLResponse("<p>Please provide comma-separated ZIP codes.</p>")

    try:
        comparison = await compare_neighborhoods(session, zips)
        html = templates.get_template("neighborhoods_compare.html").render(
            comparison=comparison,
        )
        return HTMLResponse(html)
    except Exception as exc:
        return HTMLResponse(f"<p>Error comparing neighborhoods: {exc}</p>")


# ── Outreach (NIF-203) ────────────────────────────────────────


@router.get("/outreach", response_class=HTMLResponse)
async def outreach_dashboard(
    request: Request,
    message: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """Outreach campaign list with stats."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.outreach import list_campaigns, calculate_performance

    try:
        campaigns = await list_campaigns(session)
        # Calculate performance for each campaign
        campaign_stats = []
        for campaign in campaigns:
            try:
                perf = await calculate_performance(session, campaign.id)
                campaign_stats.append({"campaign": campaign, "performance": perf})
            except Exception:
                campaign_stats.append({"campaign": campaign, "performance": None})
    except Exception as exc:
        campaigns = []
        campaign_stats = []
        message = message or f"Error loading campaigns: {exc}"

    html = templates.get_template("outreach.html").render(
        campaigns=campaigns,
        campaign_stats=campaign_stats,
        message=message,
        active_tab="outreach",
    )
    return HTMLResponse(html)


@router.post("/outreach/campaigns")
async def create_campaign_route(
    request: Request,
    name: str = Form(...),
    campaign_type: str = Form("email"),
    session: AsyncSession = Depends(get_session),
):
    """Create a new outreach campaign from the dashboard form."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.outreach import create_campaign

    try:
        campaign = await create_campaign(
            session,
            name=name,
            campaign_type=campaign_type,
            created_by="dashboard",
        )
        await session.commit()
        msg = f"Campaign '{name}' created successfully"
    except Exception as exc:
        msg = f"Error creating campaign: {exc}"

    return RedirectResponse(
        url=f"/dashboard/outreach?message={msg}",
        status_code=303,
    )


@router.get("/outreach/campaigns/{campaign_id}", response_class=HTMLResponse)
async def campaign_detail_partial(
    request: Request,
    campaign_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial — campaign detail with targets and activities."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    from src.services.outreach import get_campaign, list_targets, calculate_performance

    try:
        campaign = await get_campaign(session, campaign_id)
        if not campaign:
            return HTMLResponse("<p>Campaign not found</p>", status_code=404)

        targets = await list_targets(session, campaign_id)
        perf = await calculate_performance(session, campaign_id)

        html = templates.get_template("campaign_detail.html").render(
            campaign=campaign,
            targets=targets,
            performance=perf,
        )
        return HTMLResponse(html)
    except Exception as exc:
        return HTMLResponse(f"<p>Error loading campaign: {exc}</p>")


@router.patch("/outreach/campaigns/{campaign_id}/status", response_class=HTMLResponse)
async def update_campaign_status_route(
    request: Request,
    campaign_id: UUID,
    status: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    """HTMX endpoint — update campaign status inline."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    from src.services.outreach import update_campaign

    try:
        campaign = await update_campaign(session, campaign_id, status=status)
        await session.commit()
        return HTMLResponse(
            f'<span class="badge badge-{status}">{status}</span>'
        )
    except Exception as exc:
        return HTMLResponse(f"<span>Error: {exc}</span>", status_code=400)


@router.post("/outreach/campaigns/{campaign_id}/activity", response_class=HTMLResponse)
async def log_campaign_activity_route(
    request: Request,
    campaign_id: UUID,
    target_id: str = Form(...),
    activity_type: str = Form(...),
    outcome: str = Form(""),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """HTMX endpoint — log an outreach activity against a target."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    from src.services.outreach import log_activity

    try:
        activity = await log_activity(
            session,
            target_id=UUID(target_id),
            activity_type=activity_type,
            outcome=outcome or None,
            notes=notes or None,
            performed_by="dashboard",
        )
        await session.commit()
        return HTMLResponse(
            f'<div class="activity-logged">Activity logged: {activity_type}'
            f'{" — " + outcome if outcome else ""}</div>'
        )
    except Exception as exc:
        return HTMLResponse(f"<span>Error: {exc}</span>", status_code=400)


# ── Sales Queue (NIF-204) ─────────────────────────────────────


@router.get("/queue", response_class=HTMLResponse)
async def queue_dashboard(
    request: Request,
    rep_id: str = Query(""),
    message: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """Sales rep work queue page."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.rep_queue import get_queue, get_rep_ranking

    items = []
    ranking = None
    if rep_id:
        try:
            items = await get_queue(session, rep_id)
            ranking = await get_rep_ranking(session, rep_id)
        except Exception as exc:
            message = message or f"Error loading queue: {exc}"

    html = templates.get_template("queue.html").render(
        items=items,
        ranking=ranking,
        rep_id=rep_id,
        message=message,
        active_tab="queue",
    )
    return HTMLResponse(html)


@router.patch("/queue/items/{item_id}/claim", response_class=HTMLResponse)
async def claim_queue_item_route(
    request: Request,
    item_id: UUID,
    rep_id: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    """HTMX endpoint — claim a queue item."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    from src.services.rep_queue import claim_item

    try:
        item = await claim_item(session, item_id, rep_id)
        await session.commit()
        return HTMLResponse(
            f'<span class="badge badge-claimed">claimed</span>'
        )
    except Exception as exc:
        return HTMLResponse(f"<span>Error: {exc}</span>", status_code=400)


@router.patch("/queue/items/{item_id}/complete", response_class=HTMLResponse)
async def complete_queue_item_route(
    request: Request,
    item_id: UUID,
    outcome: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """HTMX endpoint — complete a queue item."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    from src.services.rep_queue import complete_item

    try:
        item = await complete_item(session, item_id, outcome=outcome or None)
        await session.commit()
        return HTMLResponse(
            f'<span class="badge badge-completed">completed</span>'
        )
    except Exception as exc:
        return HTMLResponse(f"<span>Error: {exc}</span>", status_code=400)


@router.patch("/queue/items/{item_id}/skip", response_class=HTMLResponse)
async def skip_queue_item_route(
    request: Request,
    item_id: UUID,
    reason: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """HTMX endpoint — skip a queue item."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    from src.services.rep_queue import skip_item

    try:
        item = await skip_item(session, item_id, reason=reason or None)
        await session.commit()
        return HTMLResponse(
            f'<span class="badge badge-skipped">skipped</span>'
        )
    except Exception as exc:
        return HTMLResponse(f"<span>Error: {exc}</span>", status_code=400)


@router.post("/queue/populate")
async def populate_queue_route(
    request: Request,
    rep_id: str = Form(...),
    city: str = Form(""),
    state: str = Form(""),
    zip_code: str = Form(""),
    min_icp_score: str = Form(""),
    fit_label: str = Form(""),
    limit: int = Form(50),
    session: AsyncSession = Depends(get_session),
):
    """Auto-populate a rep's queue from restaurant filters."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.rep_queue import populate_queue

    filters = {}
    if city:
        filters["city"] = city
    if state:
        filters["state"] = state
    if zip_code:
        filters["zip_code"] = zip_code
    if min_icp_score:
        try:
            filters["min_icp_score"] = float(min_icp_score)
        except ValueError:
            pass
    if fit_label:
        filters["fit_label"] = fit_label

    try:
        result = await populate_queue(session, rep_id, filters=filters or None, limit=limit)
        await session.commit()
        msg = f"Added {result['added']} items to {rep_id}'s queue"
    except Exception as exc:
        msg = f"Error populating queue: {exc}"

    return RedirectResponse(
        url=f"/dashboard/queue?rep_id={rep_id}&message={msg}",
        status_code=303,
    )


# ── Qualification (NIF-205) ───────────────────────────────────


@router.get("/qualification", response_class=HTMLResponse)
async def qualification_dashboard(
    request: Request,
    message: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """Qualification review page — pending reviews and stats."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.qualification import list_pending_review

    try:
        pending = await list_pending_review(session)
    except Exception as exc:
        pending = []
        message = message or f"Error loading qualifications: {exc}"

    # Stats
    total_qualified = await session.scalar(
        select(func.count(QualificationResult.id)).where(
            QualificationResult.qualification_status == "qualified"
        )
    ) or 0
    total_not_qualified = await session.scalar(
        select(func.count(QualificationResult.id)).where(
            QualificationResult.qualification_status == "not_qualified"
        )
    ) or 0
    total_needs_review = await session.scalar(
        select(func.count(QualificationResult.id)).where(
            QualificationResult.qualification_status == "needs_review"
        )
    ) or 0

    stats = {
        "qualified": total_qualified,
        "not_qualified": total_not_qualified,
        "needs_review": total_needs_review,
    }

    html = templates.get_template("qualification.html").render(
        pending=pending,
        stats=stats,
        message=message,
        active_tab="qualification",
    )
    return HTMLResponse(html)


@router.post("/qualification/evaluate/{restaurant_id}", response_class=HTMLResponse)
async def evaluate_restaurant_route(
    request: Request,
    restaurant_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTMX endpoint — trigger qualification evaluation for a restaurant."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    from src.services.qualification import qualify_restaurant

    try:
        result = await qualify_restaurant(session, restaurant_id)
        await session.commit()
        return HTMLResponse(
            f'<div class="qualification-result">'
            f'<span class="badge badge-{result.qualification_status}">'
            f'{result.qualification_status}</span> '
            f'(confidence: {result.confidence_score:.2%})</div>'
        )
    except Exception as exc:
        return HTMLResponse(f"<span>Error: {exc}</span>", status_code=400)


@router.patch("/qualification/{result_id}/review", response_class=HTMLResponse)
async def review_qualification_route(
    request: Request,
    result_id: UUID,
    decision: str = Form(...),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """HTMX endpoint — approve or reject a qualification result."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    from src.services.qualification import review_qualification

    try:
        result = await review_qualification(
            session,
            result_id=result_id,
            decision=decision,
            reviewed_by="dashboard",
            notes=notes or None,
        )
        await session.commit()
        return HTMLResponse(
            f'<span class="badge badge-{result.qualification_status}">'
            f'{result.qualification_status}</span> '
            f'(reviewed: {decision})'
        )
    except Exception as exc:
        return HTMLResponse(f"<span>Error: {exc}</span>", status_code=400)


@router.post("/qualification/batch")
async def batch_qualify_route(
    request: Request,
    city: str = Form(""),
    state: str = Form(""),
    zip_code: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Batch evaluate restaurants from filters."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.qualification import batch_qualify

    filters = {}
    if city:
        filters["city"] = city
    if state:
        filters["state"] = state
    if zip_code:
        filters["zip_code"] = zip_code

    try:
        result = await batch_qualify(session, filters=filters or None)
        await session.commit()
        msg = (
            f"Batch qualification complete: {result['total']} evaluated — "
            f"{result['qualified']} qualified, "
            f"{result['not_qualified']} not qualified, "
            f"{result['needs_review']} need review"
        )
    except Exception as exc:
        msg = f"Error in batch qualification: {exc}"

    return RedirectResponse(
        url=f"/dashboard/qualification?message={msg}",
        status_code=303,
    )


# ── Clusters (NIF-206) ────────────────────────────────────────


@router.get("/clusters", response_class=HTMLResponse)
async def clusters_dashboard(
    request: Request,
    message: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """Cluster list with stats."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.cluster_engine import list_clusters

    try:
        clusters = await list_clusters(session)
    except Exception as exc:
        clusters = []
        message = message or f"Error loading clusters: {exc}"

    # Stats
    total_clusters = await session.scalar(
        select(func.count(MerchantCluster.id))
    ) or 0
    total_members = await session.scalar(
        select(func.count(ClusterMember.id)).where(
            ClusterMember.role.in_(["anchor", "member"])
        )
    ) or 0
    avg_flywheel = await session.scalar(
        select(func.avg(MerchantCluster.flywheel_score))
    )

    stats = {
        "total_clusters": total_clusters,
        "total_members": total_members,
        "avg_flywheel": round(float(avg_flywheel), 2) if avg_flywheel else None,
    }

    html = templates.get_template("clusters.html").render(
        clusters=clusters,
        stats=stats,
        message=message,
        active_tab="clusters",
    )
    return HTMLResponse(html)


@router.post("/clusters/detect")
async def detect_clusters_route(
    request: Request,
    zip_code: str = Form(""),
    min_size: int = Form(3),
    radius_miles: float = Form(1.0),
    session: AsyncSession = Depends(get_session),
):
    """Detect merchant clusters from the dashboard form."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.cluster_engine import detect_clusters

    try:
        clusters = await detect_clusters(
            session,
            zip_code=zip_code or None,
            min_size=min_size,
            radius_miles=radius_miles,
        )
        await session.commit()
        msg = f"Detected {len(clusters)} clusters"
    except Exception as exc:
        msg = f"Error detecting clusters: {exc}"

    return RedirectResponse(
        url=f"/dashboard/clusters?message={msg}",
        status_code=303,
    )


@router.get("/clusters/{cluster_id}", response_class=HTMLResponse)
async def cluster_detail_partial(
    request: Request,
    cluster_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTMX partial — cluster detail with members and history."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    from src.services.cluster_engine import get_cluster_detail, get_cluster_history

    try:
        cluster = await get_cluster_detail(session, cluster_id)
        if not cluster:
            return HTMLResponse("<p>Cluster not found</p>", status_code=404)

        history = await get_cluster_history(session, cluster_id)

        html = templates.get_template("cluster_detail.html").render(
            cluster=cluster,
            history=history,
        )
        return HTMLResponse(html)
    except Exception as exc:
        return HTMLResponse(f"<p>Error loading cluster: {exc}</p>")


@router.post("/clusters/{cluster_id}/expansion-plan")
async def create_expansion_plan_route(
    request: Request,
    cluster_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Create an expansion plan for a cluster."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.cluster_engine import plan_expansion

    try:
        plans = await plan_expansion(session, cluster_id)
        await session.commit()
        msg = f"Created expansion plan with {len(plans)} targets"
    except Exception as exc:
        msg = f"Error creating expansion plan: {exc}"

    return RedirectResponse(
        url=f"/dashboard/clusters?message={msg}",
        status_code=303,
    )


@router.post("/clusters/{cluster_id}/launch-campaign")
async def launch_cluster_campaign_route(
    request: Request,
    cluster_id: UUID,
    campaign_type: str = Form("email"),
    session: AsyncSession = Depends(get_session),
):
    """Launch an outreach campaign from a cluster's expansion plan."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.cluster_engine import launch_campaign

    try:
        campaign = await launch_campaign(session, cluster_id, campaign_type=campaign_type)
        await session.commit()
        msg = f"Campaign '{campaign.name}' launched"
    except Exception as exc:
        msg = f"Error launching campaign: {exc}"

    return RedirectResponse(
        url=f"/dashboard/clusters?message={msg}",
        status_code=303,
    )


@router.post("/clusters/{cluster_id}/recalculate")
async def recalculate_cluster_route(
    request: Request,
    cluster_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Recalculate cluster scores and stats."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    from src.services.cluster_engine import recalculate_cluster

    try:
        cluster = await recalculate_cluster(session, cluster_id)
        await session.commit()
        msg = (
            f"Cluster '{cluster.name}' recalculated: "
            f"{cluster.restaurant_count} members, "
            f"ICP avg {cluster.avg_icp_score:.1f}, "
            f"flywheel {cluster.flywheel_score:.1f}"
        )
    except Exception as exc:
        msg = f"Error recalculating cluster: {exc}"

    return RedirectResponse(
        url=f"/dashboard/clusters?message={msg}",
        status_code=303,
    )


# ── Lead Detail Enhancements (NIF-207) ────────────────────────


@router.post("/leads/{lead_id}/tasks")
async def create_lead_task_route(
    request: Request,
    lead_id: UUID,
    title: str = Form(...),
    description: str = Form(""),
    task_type: str = Form("follow_up"),
    priority: str = Form("medium"),
    assigned_to: str = Form(""),
    due_date: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Create a follow-up task for a lead."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    # Verify lead exists
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        return RedirectResponse(url="/dashboard", status_code=303)

    task = FollowUpTask(
        lead_id=lead_id,
        title=title,
        description=description or None,
        task_type=task_type,
        priority=priority,
        status="pending",
        assigned_to=assigned_to or None,
    )

    if due_date:
        try:
            task.due_date = datetime.fromisoformat(due_date).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    session.add(task)
    await session.commit()

    return RedirectResponse(
        url=f"/dashboard/leads/{lead_id}",
        status_code=303,
    )


@router.patch("/leads/tasks/{task_id}/complete", response_class=HTMLResponse)
async def complete_lead_task_route(
    request: Request,
    task_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """HTMX endpoint — mark a follow-up task as completed."""
    if not _require_login(request):
        return HTMLResponse("Unauthorized", status_code=401)

    task = await session.get(FollowUpTask, task_id)
    if not task:
        return HTMLResponse("Task not found", status_code=404)

    task.status = "completed"
    task.completed_at = datetime.now(timezone.utc)
    await session.commit()

    return HTMLResponse(
        f'<span class="badge badge-completed">completed</span>'
    )


@router.post("/leads/{lead_id}/merge")
async def merge_lead_route(
    request: Request,
    lead_id: UUID,
    source_lead_id: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    """Merge another lead into this one."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    target_result = await session.execute(select(Lead).where(Lead.id == lead_id))
    target_lead = target_result.scalar_one_or_none()
    if not target_lead:
        return RedirectResponse(url="/dashboard", status_code=303)

    try:
        source_id = UUID(source_lead_id)
    except ValueError:
        return RedirectResponse(
            url=f"/dashboard/leads/{lead_id}",
            status_code=303,
        )

    source_result = await session.execute(select(Lead).where(Lead.id == source_id))
    source_lead = source_result.scalar_one_or_none()
    if not source_lead:
        return RedirectResponse(
            url=f"/dashboard/leads/{lead_id}",
            status_code=303,
        )

    # Merge: move tasks, stage history, and assignment history to target lead
    await session.execute(
        select(FollowUpTask).where(FollowUpTask.lead_id == source_id)
    )
    tasks_result = await session.execute(
        select(FollowUpTask).where(FollowUpTask.lead_id == source_id)
    )
    for task in tasks_result.scalars().all():
        task.lead_id = lead_id

    stage_result = await session.execute(
        select(LeadStageHistory).where(LeadStageHistory.lead_id == source_id)
    )
    for entry in stage_result.scalars().all():
        entry.lead_id = lead_id

    assignment_result = await session.execute(
        select(LeadAssignmentHistory).where(LeadAssignmentHistory.lead_id == source_id)
    )
    for entry in assignment_result.scalars().all():
        entry.lead_id = lead_id

    # Fill in empty fields from source
    for field in ["company", "business_type", "phone", "interest", "restaurant_id", "account_id", "contact_id"]:
        if not getattr(target_lead, field) and getattr(source_lead, field):
            setattr(target_lead, field, getattr(source_lead, field))

    # Record audit log
    audit = AuditLog(
        action="lead_merged",
        entity_type="lead",
        details={
            "lead_id": str(lead_id),
            "source_lead_id": str(source_id),
            "source_email": source_lead.email,
        },
        performed_by="dashboard",
    )
    session.add(audit)

    # Mark source as merged
    source_lead.status = "merged"

    await session.commit()

    return RedirectResponse(
        url=f"/dashboard/leads/{lead_id}",
        status_code=303,
    )


# ── Communications Analytics (NIF-239) ──────────────────────


@router.get("/communications", response_class=HTMLResponse)
async def communications_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Communication analytics dashboard — email/SMS engagement metrics."""
    if not _require_login(request):
        return RedirectResponse(url="/dashboard/login", status_code=303)
    from sqlalchemy import cast, Date

    # Overall stats from TrackerEvent
    total_sent = await session.scalar(
        select(func.count(TrackerEvent.id)).where(
            TrackerEvent.event_type.in_(["delivery", "send"])
        )
    ) or 0
    total_opens = await session.scalar(
        select(func.count(TrackerEvent.id)).where(
            TrackerEvent.event_type == "open"
        )
    ) or 0
    total_clicks = await session.scalar(
        select(func.count(TrackerEvent.id)).where(
            TrackerEvent.event_type == "click"
        )
    ) or 0
    total_bounces = await session.scalar(
        select(func.count(TrackerEvent.id)).where(
            TrackerEvent.event_type == "bounce"
        )
    ) or 0
    total_replies = await session.scalar(
        select(func.count(OutreachActivity.id)).where(
            OutreachActivity.outcome == "replied"
        )
    ) or 0

    open_rate = (total_opens / total_sent * 100) if total_sent > 0 else 0
    click_rate = (total_clicks / total_sent * 100) if total_sent > 0 else 0
    reply_rate = (total_replies / total_sent * 100) if total_sent > 0 else 0
    bounce_rate = (total_bounces / total_sent * 100) if total_sent > 0 else 0

    stats = {
        "total_sent": total_sent,
        "total_opens": total_opens,
        "total_clicks": total_clicks,
        "total_bounces": total_bounces,
        "total_replies": total_replies,
        "open_rate": round(open_rate, 1),
        "click_rate": round(click_rate, 1),
        "reply_rate": round(reply_rate, 1),
        "bounce_rate": round(bounce_rate, 1),
    }

    # Timeline: emails per day for last 30 days
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    time_rows = await session.execute(
        select(
            cast(TrackerEvent.occurred_at, Date).label("day"),
            func.count(TrackerEvent.id).label("cnt"),
        )
        .where(TrackerEvent.occurred_at >= thirty_days_ago)
        .where(TrackerEvent.event_type.in_(["delivery", "send"]))
        .group_by("day")
        .order_by("day")
    )
    raw_timeline = {str(r[0]): r[1] for r in time_rows.all()}
    timeline = []
    for i in range(31):
        d = (thirty_days_ago + timedelta(days=i)).strftime("%Y-%m-%d")
        timeline.append({"date": d[5:], "count": raw_timeline.get(d, 0)})

    # Top engaged leads — leads with highest engagement event counts
    top_leads_rows = await session.execute(
        select(
            Lead.id,
            Lead.email,
            Lead.company,
            Lead.first_name,
            Lead.last_name,
            func.count(TrackerEvent.id).label("event_count"),
        )
        .join(TrackerEvent, TrackerEvent.lead_id == Lead.id)
        .group_by(Lead.id, Lead.email, Lead.company, Lead.first_name, Lead.last_name)
        .order_by(func.count(TrackerEvent.id).desc())
        .limit(15)
    )
    top_leads = [
        {
            "id": str(r[0]),
            "email": r[1],
            "company": r[2],
            "name": f"{r[3] or ''} {r[4] or ''}".strip(),
            "event_count": r[5],
        }
        for r in top_leads_rows.all()
    ]

    # Campaign performance summary
    campaign_rows = await session.execute(
        select(
            OutreachCampaign.id,
            OutreachCampaign.name,
            OutreachCampaign.status,
            OutreachCampaign.campaign_type,
            func.count(TrackerEvent.id).label("event_count"),
        )
        .outerjoin(TrackerEvent, TrackerEvent.campaign_id == OutreachCampaign.id)
        .group_by(OutreachCampaign.id, OutreachCampaign.name, OutreachCampaign.status, OutreachCampaign.campaign_type)
        .order_by(func.count(TrackerEvent.id).desc())
        .limit(10)
    )
    campaigns = [
        {
            "id": str(r[0]),
            "name": r[1],
            "status": r[2],
            "type": r[3],
            "event_count": r[4],
        }
        for r in campaign_rows.all()
    ]

    # Recent communication events
    recent_events_result = await session.execute(
        select(TrackerEvent)
        .order_by(TrackerEvent.occurred_at.desc())
        .limit(20)
    )
    recent_events = recent_events_result.scalars().all()

    html = templates.get_template("communications.html").render(
        stats=stats,
        timeline=timeline,
        top_leads=top_leads,
        campaigns=campaigns,
        recent_events=recent_events,
        active_tab="communications",
    )
    return HTMLResponse(html)
