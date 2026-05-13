#!/bin/bash
set -e

# Run migrations here, not in build.sh — the build phase isn't on the private
# network so internal DB hostnames don't resolve there. Only the web service
# runs migrations; the worker/beat services must not race against this.
echo "Running database migrations..."
alembic upgrade head

echo "Starting N4Cluster ICP Finder..."
exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}"
