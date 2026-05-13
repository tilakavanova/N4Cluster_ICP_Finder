#!/bin/bash
set -e

echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Migrations are run at service startup (see scripts/start.sh), NOT during build.
# Render's build environment is not on the private network, so internal Postgres
# hostnames (dpg-xxxxx-a) won't resolve here and the build would fail with
# `socket.gaierror: Name or service not known`.

echo "Build complete."
