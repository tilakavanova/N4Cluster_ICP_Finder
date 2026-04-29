"""FastAPI application factory."""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from src.config import settings
from src.utils.logging import setup_logging, get_logger
from src.api.routers import restaurants, jobs, scores, seed, leads, changes, analytics, calculator, crm, neighborhoods, merchant_graph, scoring_engine, configuration, outreach, qualification, rep_queue, conversion_analytics, cluster_engine
from src.api.routers import tracking as tracking_router
from src.api.routers import webhooks as webhooks_router
from src.api.routers import hubspot_webhooks as hubspot_webhooks_router
from src.api.routers import unsubscribe as unsubscribe_router
from src.api.routers import auth as auth_router
from src.api.routers import compliance as compliance_router
from src.api.routers import feedback_loop as feedback_loop_router
from src.dashboard.routes import router as dashboard_router

logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    setup_logging()
    logger.info("application_starting", debug=settings.debug)
    if settings.allow_seed_routes:
        logger.warning("seed_routes_enabled", message="Seed routes are ENABLED — disable in production!")
    yield
    logger.info("application_shutting_down")


app = FastAPI(
    title="N4Cluster ICP Finder",
    description="Restaurant ICP (Ideal Customer Profile) identification and scoring system",
    version="0.1.0",
    lifespan=lifespan,
)

# Session middleware for dashboard login
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key or "dev-secret-change-me",
    session_cookie="icp_session",
    max_age=8 * 60 * 60,  # 8 hours
    same_site="lax",
    https_only=not settings.debug,
)

# CORS — restricted to allowed origins (debug mode allows all)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if not settings.debug:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", error=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# Mount routers
app.include_router(restaurants.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(scores.router, prefix="/api/v1")
if settings.allow_seed_routes:
    app.include_router(seed.router, prefix="/api/v1")
app.include_router(leads.router, prefix="/api/v1")
app.include_router(changes.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(calculator.router, prefix="/api/v1")
app.include_router(crm.router, prefix="/api/v1")
app.include_router(neighborhoods.router, prefix="/api/v1")
app.include_router(merchant_graph.router, prefix="/api/v1")
app.include_router(scoring_engine.router, prefix="/api/v1")
app.include_router(configuration.router, prefix="/api/v1")
app.include_router(outreach.router, prefix="/api/v1")
app.include_router(qualification.router, prefix="/api/v1")
app.include_router(rep_queue.router, prefix="/api/v1")
app.include_router(conversion_analytics.router, prefix="/api/v1")
app.include_router(cluster_engine.router, prefix="/api/v1")
app.include_router(auth_router.router, prefix="/api/v1")
app.include_router(compliance_router.router, prefix="/api/v1")
app.include_router(feedback_loop_router.router, prefix="/api/v1")
app.include_router(tracking_router.router)  # /t/{token} and /px/{token}.gif — no prefix
app.include_router(webhooks_router.router)  # /webhooks/sendgrid, /webhooks/sendgrid/inbound
app.include_router(hubspot_webhooks_router.router)  # /webhooks/hubspot
app.include_router(unsubscribe_router.router)  # /unsubscribe/{token}, /unsubscribe/one-click
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.1.0"}


@app.get("/api/v1/admin/llm-usage")
async def llm_usage():
    """Return daily LLM token usage for cost monitoring."""
    from src.extraction.llm_client import get_daily_usage
    return get_daily_usage()


def run():
    """Entry point for CLI."""
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=settings.debug)
