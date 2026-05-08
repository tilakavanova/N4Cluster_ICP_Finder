"""Celery tasks for campaign anomaly detection (NIF-267)."""

import asyncio

from src.tasks.celery_app import celery_app
from src.utils.logging import get_logger

logger = get_logger("anomaly_tasks")


@celery_app.task(name="check_all_campaigns_health")
def check_all_campaigns_health() -> dict:
    """Periodic task: check all active campaigns for anomalies.

    Runs every 30 minutes via celery beat schedule.
    """
    from src.db.session import async_session_factory
    from src.services.anomaly_detection import check_all_campaigns

    async def _run():
        async with async_session_factory() as session:
            result = await check_all_campaigns(session)
            await session.commit()
            return result

    try:
        result = asyncio.get_event_loop().run_until_complete(_run())
        logger.info("campaign_health_check_task_complete", result=result)
        return result
    except Exception as exc:
        logger.error("campaign_health_check_task_failed", error=str(exc))
        return {"error": str(exc)}
