#!/usr/bin/env bash
set -o errexit

# Ensure src package is importable
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"

# Auto-restart wrapper for background processes
run_with_restart() {
    local name="$1"
    shift
    while true; do
        echo "=== Starting $name ==="
        "$@" 2>&1 || true
        echo "WARNING: $name exited — restarting in 5s..."
        sleep 5
    done
}

# Start Celery worker with auto-restart
run_with_restart "Celery worker" \
    celery -A src.tasks.celery_app worker --loglevel=info --concurrency=2 &
echo "Celery worker supervisor started (PID: $!)"

# Start Celery beat with auto-restart
run_with_restart "Celery beat" \
    celery -A src.tasks.celery_app beat --loglevel=info &
echo "Celery beat supervisor started (PID: $!)"

# Give Celery a moment to initialize
sleep 3

echo "=== Starting FastAPI server ==="
uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}
