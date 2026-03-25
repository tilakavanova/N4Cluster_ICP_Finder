#!/usr/bin/env bash
set -o errexit

# Upgrade pip and setuptools first to avoid stale cache
pip install --upgrade pip setuptools wheel

# Install the project
pip install -e "."

# Install Playwright browsers (skip if no root — e.g. Render free tier)
playwright install chromium 2>/dev/null || echo "Playwright browser install skipped (no root). Crawlers will use httpx fallback."

# Run migrations
if [ -n "$DATABASE_URL" ]; then
    alembic upgrade head
fi
