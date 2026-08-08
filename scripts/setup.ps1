# Engineering Spend Intelligence — native setup (Windows / PowerShell).
# Owner: shared infra. Phase: Tier 0.
# Functional equivalent of scripts/setup.sh — keep the two in sync.
#
# Starts Postgres via Docker (even in the "native" path, to avoid a local
# Postgres install), waits for it, applies the schema, then installs
# backend + frontend deps.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example — review it before ingesting real data."
}

Write-Host "Starting Postgres..."
docker compose -f infra/docker-compose.yml up -d postgres

Write-Host "Waiting for Postgres to be healthy..."
$ready = $false
while (-not $ready) {
    docker compose -f infra/docker-compose.yml exec -T postgres pg_isready -U esi -d esi *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true } else { Start-Sleep -Seconds 1 }
}

Write-Host "Applying schema..."
docker compose -f infra/docker-compose.yml exec -T postgres `
    psql -U esi -d esi -f /docker-entrypoint-initdb.d/001_schema.sql

Write-Host "Installing backend dependencies..."
python -m venv backend\.venv
& backend\.venv\Scripts\pip.exe install --upgrade pip
& backend\.venv\Scripts\pip.exe install -e ".\backend[dev]"

Write-Host "Installing frontend dependencies..."
Push-Location frontend
npm install
Pop-Location

Write-Host "Setup complete. See README.md for how to run the app."
