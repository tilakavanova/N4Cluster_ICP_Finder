#!/usr/bin/env bash
set -o errexit

pip install -e "."
playwright install chromium --with-deps

# Run migrations
if [ -n "$DATABASE_URL" ]; then
    # Convert postgres:// to postgresql:// for alembic (sync driver)
    export ALEMBIC_DB_URL=$(echo "$DATABASE_URL" | sed 's|^postgres://|postgresql://|')
    alembic upgrade head
fi
