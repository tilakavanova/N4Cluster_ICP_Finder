"""Prometheus metrics export endpoint (NIF-268).

Exposes application metrics in Prometheus text exposition format at GET /metrics.
No external library required — builds the text format directly.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, func

from src.db.models import Restaurant, Lead, ICPScore, OutreachCampaign
from src.db.session import get_session
from src.utils.logging import get_logger

logger = get_logger("metrics")

router = APIRouter(tags=["metrics"])

# ── In-memory request counters (lightweight alternative to prometheus_client) ──

_request_counts: dict[str, int] = {}
_request_duration_sum: dict[str, float] = {}


def record_request(method: str, path: str, status: int, duration_s: float) -> None:
    """Record a request metric (called from middleware)."""
    key = f'{method}:{path}:{status}'
    _request_counts[key] = _request_counts.get(key, 0) + 1
    _request_duration_sum[key] = _request_duration_sum.get(key, 0.0) + duration_s


def _format_metric(name: str, help_text: str, metric_type: str, samples: list[tuple[str, float]]) -> str:
    """Format a single metric in Prometheus text exposition format."""
    lines = [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} {metric_type}",
    ]
    for labels, value in samples:
        if labels:
            lines.append(f"{name}{{{labels}}} {value}")
        else:
            lines.append(f"{name} {value}")
    return "\n".join(lines)


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    """Export application metrics in Prometheus text exposition format."""
    sections: list[str] = []

    # Request counters
    req_samples = []
    for key, count in _request_counts.items():
        method, path, status = key.split(":", 2)
        req_samples.append((f'method="{method}",path="{path}",status="{status}"', float(count)))
    if req_samples:
        sections.append(_format_metric(
            "http_requests_total",
            "Total number of HTTP requests",
            "counter",
            req_samples,
        ))

    # Request duration
    dur_samples = []
    for key, total in _request_duration_sum.items():
        method, path, status = key.split(":", 2)
        dur_samples.append((f'method="{method}",path="{path}",status="{status}"', total))
    if dur_samples:
        sections.append(_format_metric(
            "http_request_duration_seconds_total",
            "Total request duration in seconds",
            "counter",
            dur_samples,
        ))

    # Database metrics (use a fresh session)
    try:
        async for session in get_session():
            # Restaurants total
            restaurants_total = (await session.execute(
                select(func.count()).select_from(Restaurant)
            )).scalar() or 0
            sections.append(_format_metric(
                "restaurants_total", "Total number of restaurants", "gauge",
                [("", float(restaurants_total))],
            ))

            # Leads total
            leads_total = (await session.execute(
                select(func.count()).select_from(Lead)
            )).scalar() or 0
            sections.append(_format_metric(
                "leads_total", "Total number of leads", "gauge",
                [("", float(leads_total))],
            ))

            # Active campaigns
            active_campaigns = (await session.execute(
                select(func.count()).select_from(OutreachCampaign).where(
                    OutreachCampaign.status == "active"
                )
            )).scalar() or 0
            sections.append(_format_metric(
                "active_campaigns", "Number of active outreach campaigns", "gauge",
                [("", float(active_campaigns))],
            ))

            # Average ICP score
            avg_icp = (await session.execute(
                select(func.avg(ICPScore.total_icp_score))
            )).scalar() or 0.0
            sections.append(_format_metric(
                "icp_scores_avg", "Average ICP score across all restaurants", "gauge",
                [("", round(float(avg_icp), 2))],
            ))

            break  # only need one session iteration
    except Exception as exc:
        logger.warning("metrics_db_query_failed", error=str(exc))
        # Still return request metrics even if DB is unavailable
        sections.append(f"# DB metrics unavailable: {exc}")

    return "\n\n".join(sections) + "\n"
