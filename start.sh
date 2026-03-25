#!/usr/bin/env bash
set -o errexit

# Start Celery worker in the background
celery -A src.tasks.celery_app worker --loglevel=info --concurrency=2 &

# Start Celery beat scheduler in the background
celery -A src.tasks.celery_app beat --loglevel=info &

# Start the FastAPI server (foreground)
uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}
