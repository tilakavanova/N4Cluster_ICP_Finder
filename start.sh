#!/usr/bin/env bash
set -o errexit

# Ensure src package is importable
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"

echo "=== Starting Celery worker ==="
celery -A src.tasks.celery_app worker --loglevel=info --concurrency=2 2>&1 &
WORKER_PID=$!
echo "Celery worker started (PID: $WORKER_PID)"

echo "=== Starting Celery beat ==="
celery -A src.tasks.celery_app beat --loglevel=info 2>&1 &
BEAT_PID=$!
echo "Celery beat started (PID: $BEAT_PID)"

# Give Celery a moment to fail fast if there's a config issue
sleep 3

# Check if worker is still alive
if ! kill -0 $WORKER_PID 2>/dev/null; then
    echo "ERROR: Celery worker crashed on startup!"
    wait $WORKER_PID 2>/dev/null || true
fi

if ! kill -0 $BEAT_PID 2>/dev/null; then
    echo "ERROR: Celery beat crashed on startup!"
    wait $BEAT_PID 2>/dev/null || true
fi

echo "=== Starting FastAPI server ==="
# Start the FastAPI server (foreground)
uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}
