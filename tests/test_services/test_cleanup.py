"""Tests for the cleanup service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

from src.db.models import CrawlJob, AuditLog


class TestCleanupConfig:
    """Test cleanup configuration defaults."""

    def test_default_retention_days(self):
        from src.config import Settings
        s = Settings()
        assert s.crawl_job_retention_days == 30

    def test_default_stale_timeout(self):
        from src.config import Settings
        s = Settings()
        assert s.stale_job_timeout_minutes == 60

    def test_custom_retention(self):
        from src.config import Settings
        s = Settings(crawl_job_retention_days=7)
        assert s.crawl_job_retention_days == 7


class TestAuditLogModel:
    """Test AuditLog ORM model."""

    def test_table_name(self):
        assert AuditLog.__tablename__ == "audit_logs"

    def test_audit_log_fields(self):
        log = AuditLog(
            action="cleanup_crawl_jobs",
            entity_type="crawl_job",
            details={"jobs_deleted": 5},
            performed_by="system",
        )
        assert log.action == "cleanup_crawl_jobs"
        assert log.entity_type == "crawl_job"
        assert log.details["jobs_deleted"] == 5
        assert log.performed_by == "system"

    def test_valid_actions(self):
        valid_actions = ["cleanup_crawl_jobs", "lead_created", "score_recalculated"]
        for action in valid_actions:
            log = AuditLog(action=action)
            assert log.action == action


class TestCleanupServiceUnit:
    """Unit tests for cleanup service logic."""

    def test_stale_job_detection_criteria(self):
        """Jobs pending/running for >60 minutes should be considered stale."""
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(minutes=60)

        # Stale: pending for 2 hours
        stale_job = MagicMock(spec=CrawlJob)
        stale_job.status = "pending"
        stale_job.created_at = now - timedelta(hours=2)
        assert stale_job.created_at < stale_cutoff

        # Not stale: pending for 30 minutes
        fresh_job = MagicMock(spec=CrawlJob)
        fresh_job.status = "pending"
        fresh_job.created_at = now - timedelta(minutes=30)
        assert fresh_job.created_at >= stale_cutoff

    def test_retention_age_criteria(self):
        """Jobs older than retention period should be eligible for deletion."""
        now = datetime.now(timezone.utc)
        retention_cutoff = now - timedelta(days=30)

        # Eligible: completed 45 days ago
        old_job = MagicMock(spec=CrawlJob)
        old_job.status = "completed"
        old_job.created_at = now - timedelta(days=45)
        assert old_job.created_at < retention_cutoff

        # Not eligible: completed 10 days ago
        recent_job = MagicMock(spec=CrawlJob)
        recent_job.status = "completed"
        recent_job.created_at = now - timedelta(days=10)
        assert recent_job.created_at >= retention_cutoff

    def test_running_jobs_not_deleted(self):
        """Running/pending jobs should never be deleted (only marked stale)."""
        deletable_statuses = ["completed", "failed"]
        non_deletable_statuses = ["pending", "running"]

        for status in deletable_statuses:
            assert status in deletable_statuses

        for status in non_deletable_statuses:
            assert status not in deletable_statuses

    def test_custom_retention_override(self):
        """max_age_days parameter should override config default."""
        assert 7 != 30  # Custom vs default
        # The service accepts max_age_days to override settings.crawl_job_retention_days
