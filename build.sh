#!/usr/bin/env bash
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

# Ensure src package is importable
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"

# Run migrations
if [ -n "$DATABASE_URL" ]; then
    alembic upgrade head
fi
