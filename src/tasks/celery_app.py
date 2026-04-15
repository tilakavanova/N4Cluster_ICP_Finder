"""Celery application configuration."""

from celery import Celery
from celery.schedules import crontab
from src.config import settings

celery_app = Celery(
    "icp_finder",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "src.tasks.crawl_tasks",
        "src.tasks.extract_tasks",
        "src.tasks.score_tasks",
        "src.tasks.cleanup_tasks",
        "src.tasks.tracking_tasks",
        "src.tasks.email_tasks",
        "src.tasks.hubspot_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=300,
    task_time_limit=600,
    task_default_retry_delay=60,
    task_max_retries=3,
)

# Celery Beat schedule
celery_app.conf.beat_schedule = {
    "daily-incremental-crawl": {
        "task": "src.tasks.crawl_tasks.run_daily_crawl",
        "schedule": crontab(hour=2, minute=0),  # 2 AM UTC
        "args": (),
    },
    "weekly-full-rescore": {
        "task": "src.tasks.score_tasks.rescore_all",
        "schedule": crontab(hour=4, minute=0, day_of_week=0),  # Sunday 4 AM
        "args": (),
    },
    "daily-cleanup-old-jobs": {
        "task": "cleanup_old_jobs",
        "schedule": crontab(hour=3, minute=0),  # 3 AM UTC daily
        "args": (),
    },
}
