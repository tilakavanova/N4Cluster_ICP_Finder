"""FastAPI application factory."""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import settings
from src.utils.logging import setup_logging, get_logger
from src.api.routers import restaurants, jobs, scores, seed, leads

logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    setup_logging()
    logger.info("application_starting", debug=settings.debug)
    yield
    logger.info("application_shutting_down")


app = FastAPI(
    title="N4Cluster ICP Finder",
    description="Restaurant ICP (Ideal Customer Profile) identification and scoring system",
    version="0.1.0",
    lifespan=lifespan,
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
app.include_router(seed.router, prefix="/api/v1")
app.include_router(leads.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.1.0"}


def run():
    """Entry point for CLI."""
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=settings.debug)
