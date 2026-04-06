"""Celery tasks for scheduled cleanup operations."""

import asyncio
from src.tasks.celery_app import celery_app
from src.utils.logging import get_logger

logger = get_logger("tasks.cleanup")


@celery_app.task(bind=True, name="cleanup_old_jobs", max_retries=1)
def cleanup_old_jobs(self):
    """Scheduled task: clean up old crawl jobs, stale jobs, and orphaned records."""
    logger.info("cleanup_task_started")

    async def _run():
        from src.db.session import async_session
        from src.services.cleanup import CleanupService

        async with async_session() as session:
            try:
                service = CleanupService(session)
                result = await service.run_full_cleanup(performed_by="celery_beat")
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise

    result = asyncio.get_event_loop().run_until_complete(_run())
    logger.info("cleanup_task_finished", **result)
    return result
