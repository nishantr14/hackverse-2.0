#!/usr/bin/env bash
# Engineering Spend Intelligence — native setup (Linux / macOS).
# Owner: shared infra. Phase: Tier 0.
# Functional equivalent of scripts/setup.ps1 — keep the two in sync.
#
# Starts Postgres via Docker (even in the "native" path, to avoid a local
# Postgres install), waits for it, verifies the schema is present, then
# installs backend + frontend deps.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMPOSE=(docker compose -f infra/docker-compose.yml)
if [ -f infra/docker-compose.override.yml ]; then
  COMPOSE+=(-f infra/docker-compose.override.yml)
  echo "Using local infra/docker-compose.override.yml."
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — review it before ingesting real data."
fi

echo "Starting Postgres..."
"${COMPOSE[@]}" up -d postgres

echo "Waiting for Postgres to be healthy..."
until "${COMPOSE[@]}" exec -T postgres pg_isready -U esi -d esi >/dev/null 2>&1; do
  sleep 1
done

# docs/schema.sql is mounted into /docker-entrypoint-initdb.d and applied by
# Postgres itself, but ONLY on a first boot with an empty pgdata volume. Do not
# re-apply it here: on an existing volume every CREATE errors, and psql exits 0
# anyway, so the noise looks like success. Verify instead.
echo "Verifying schema..."
if ! "${COMPOSE[@]}" exec -T postgres \
     psql -U esi -d esi -tAc "SELECT to_regclass('public.event_log')" | grep -q event_log; then
  echo "ERROR: schema not applied. The pgdata volume predates docs/schema.sql." >&2
  echo "Recreate it (THIS DELETES ALL INGESTED DATA):" >&2
  echo "  ${COMPOSE[*]} down -v && ${COMPOSE[*]} up -d postgres" >&2
  exit 1
fi
echo "Schema present."

echo "Installing backend dependencies..."
python3 -m venv backend/.venv
backend/.venv/bin/pip install --upgrade pip
backend/.venv/bin/pip install -e "./backend[dev]"

echo "Installing frontend dependencies..."
(cd frontend && npm install)

echo
echo "Setup complete. Verify with:  cd backend && .venv/bin/pytest -q"
