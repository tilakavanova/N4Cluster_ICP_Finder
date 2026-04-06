#!/bin/bash
set -e

echo "Starting N4Cluster ICP Finder..."
exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}"
