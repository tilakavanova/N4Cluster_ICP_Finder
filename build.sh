#!/usr/bin/env bash
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

# Run migrations
if [ -n "$DATABASE_URL" ]; then
    alembic upgrade head
fi
