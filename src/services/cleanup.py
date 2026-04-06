"""Cleanup service — removes old crawl jobs, marks stale jobs, cleans orphans."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import CrawlJob, SourceRecord, Restaurant, AuditLog
from src.utils.logging import get_logger

logger = get_logger("services.cleanup")


class CleanupService:
    """Handles cleanup of old crawl jobs, stale jobs, and orphaned records."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def run_full_cleanup(
        self,
        max_age_days: int | None = None,
        performed_by: str = "system",
    ) -> dict:
        """Run all cleanup tasks and return summary.

        Args:
            max_age_days: Override retention period (defaults to config).
            performed_by: Who triggered the cleanup (system / admin / api).
        """
        retention_days = max_age_days or settings.crawl_job_retention_days
        start = datetime.now(timezone.utc)

        stale_marked = await self._mark_stale_jobs()
        jobs_deleted = await self._delete_old_jobs(retention_days)
        orphans_cleaned = await self._clean_orphaned_records()

        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        result = {
            "jobs_deleted": jobs_deleted,
            "stale_marked": stale_marked,
            "orphans_cleaned": orphans_cleaned,
            "retention_days": retention_days,
            "elapsed_ms": elapsed_ms,
        }

        # Write audit log
        audit = AuditLog(
            action="cleanup_crawl_jobs",
            entity_type="crawl_job",
            details=result,
            performed_by=performed_by,
        )
        self.session.add(audit)

        logger.info("cleanup_complete", **result, performed_by=performed_by)
        return result

    async def _mark_stale_jobs(self) -> int:
        """Mark pending/running jobs as failed if stuck beyond timeout."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.stale_job_timeout_minutes)

        result = await self.session.execute(
            update(CrawlJob)
            .where(
                CrawlJob.status.in_(["pending", "running"]),
                CrawlJob.created_at < cutoff,
            )
            .values(
                status="failed",
                error_message=f"Timed out - marked as failed by cleanup task (>{settings.stale_job_timeout_minutes} min)",
                finished_at=datetime.now(timezone.utc),
            )
        )
        count = result.rowcount
        if count:
            logger.info("stale_jobs_marked", count=count, timeout_minutes=settings.stale_job_timeout_minutes)
        return count

    async def _delete_old_jobs(self, retention_days: int) -> int:
        """Delete completed/failed jobs older than retention period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        result = await self.session.execute(
            delete(CrawlJob)
            .where(
                CrawlJob.status.in_(["completed", "failed"]),
                CrawlJob.created_at < cutoff,
            )
        )
        count = result.rowcount
        if count:
            logger.info("old_jobs_deleted", count=count, retention_days=retention_days)
        return count

    async def _clean_orphaned_records(self) -> int:
        """Delete source_records not linked to any restaurant."""
        # Find source_records where restaurant_id doesn't exist in restaurants table
        orphan_query = (
            select(SourceRecord.id)
            .outerjoin(Restaurant, SourceRecord.restaurant_id == Restaurant.id)
            .where(Restaurant.id.is_(None))
        )
        orphan_result = await self.session.execute(orphan_query)
        orphan_ids = [row[0] for row in orphan_result.all()]

        if orphan_ids:
            await self.session.execute(
                delete(SourceRecord).where(SourceRecord.id.in_(orphan_ids))
            )
            logger.info("orphaned_records_cleaned", count=len(orphan_ids))

        return len(orphan_ids)
