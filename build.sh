#!/usr/bin/env bash
set -o errexit

pip install --upgrade pip
pip install -e "."

# Install Playwright browsers (skip if no root — e.g. Render free tier)
playwright install chromium 2>/dev/null || echo "Playwright browser install skipped (no root). Crawlers will use httpx fallback."

# Run migrations
if [ -n "$DATABASE_URL" ]; then
    alembic upgrade head
fi
