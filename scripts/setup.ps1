# Engineering Spend Intelligence — native setup (Windows / PowerShell).
# Owner: shared infra. Phase: Tier 0.
# Functional equivalent of scripts/setup.sh — keep the two in sync.
#
# Starts Postgres via Docker (even in the "native" path, to avoid a local
# Postgres install), waits for it, verifies the schema is present, then
# installs backend + frontend deps.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$compose = @("-f", "infra/docker-compose.yml")
if (Test-Path "infra/docker-compose.override.yml") {
    $compose += @("-f", "infra/docker-compose.override.yml")
    Write-Host "Using local infra/docker-compose.override.yml."
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example — review it before ingesting real data."
}

Write-Host "Starting Postgres..."
docker compose @compose up -d postgres

Write-Host "Waiting for Postgres to be healthy..."
$ready = $false
while (-not $ready) {
    docker compose @compose exec -T postgres pg_isready -U esi -d esi *> $null
    if ($?) { $ready = $true } else { Start-Sleep -Seconds 1 }
}

# docs/schema.sql is mounted into /docker-entrypoint-initdb.d and applied by
# Postgres itself, but ONLY on a first boot with an empty pgdata volume. Do not
# re-apply it here: on an existing volume every CREATE errors, and psql exits 0
# anyway, so the noise looks like success. Verify instead.
Write-Host "Verifying schema..."
$table = docker compose @compose exec -T postgres psql -U esi -d esi -tAc "SELECT to_regclass('public.event_log')"
if ($table -notmatch "event_log") {
    Write-Host "ERROR: schema not applied. The pgdata volume predates docs/schema.sql." -ForegroundColor Red
    Write-Host "Recreate it (THIS DELETES ALL INGESTED DATA):" -ForegroundColor Red
    Write-Host "  docker compose $($compose -join ' ') down -v; docker compose $($compose -join ' ') up -d postgres"
    exit 1
}
Write-Host "Schema present."

Write-Host "Installing backend dependencies..."
python -m venv backend\.venv
& backend\.venv\Scripts\pip.exe install --upgrade pip
& backend\.venv\Scripts\pip.exe install -e ".\backend[dev]"

Write-Host "Installing frontend dependencies..."
Push-Location frontend
npm install
Pop-Location

Write-Host ""
Write-Host "Setup complete. Verify with:  cd backend; .venv\Scripts\pytest.exe -q"
