#!/usr/bin/env bash
# Engineering Spend Intelligence — native setup (Linux / macOS).
# Owner: shared infra. Phase: Tier 0.
# Functional equivalent of scripts/setup.ps1 — keep the two in sync.
#
# Starts Postgres via Docker (even in the "native" path, to avoid a local
# Postgres install), waits for it, applies the schema, then installs
# backend + frontend deps.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — review it before ingesting real data."
fi

echo "Starting Postgres..."
docker compose -f infra/docker-compose.yml up -d postgres

echo "Waiting for Postgres to be healthy..."
until docker compose -f infra/docker-compose.yml exec -T postgres pg_isready -U esi -d esi >/dev/null 2>&1; do
  sleep 1
done

echo "Applying schema..."
docker compose -f infra/docker-compose.yml exec -T postgres \
  psql -U esi -d esi -f /docker-entrypoint-initdb.d/001_schema.sql

echo "Installing backend dependencies..."
python3 -m venv backend/.venv
backend/.venv/bin/pip install --upgrade pip
backend/.venv/bin/pip install -e "./backend[dev]"

echo "Installing frontend dependencies..."
(cd frontend && npm install)

echo "Setup complete. See README.md for how to run the app."
