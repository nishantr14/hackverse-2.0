# Engineering Spend Intelligence

Process mining for the software development lifecycle: a priced unit of
work, joining GitHub activity, real Apache Jira data, synthetic
calendar/meeting time, and role-band rates into one event log, then
computing cost, waste, and a counterfactual "what-if" simulator on top of
it. Built for HackVerse 2.0 (IBM × Celonis × 1M1B, "AI for Business
Transformation" track).

Data ingested from two real repos and their real Jira projects:
`apache/kafka` (KAFKA) and `apache/cassandra` (CASSANDRA). Calendar/meeting
time and AI token usage are synthetic. See
[`.claude/CLAUDE.md`](.claude/CLAUDE.md) for the full real-vs-synthetic
breakdown, the determinism and privacy rules, and the frozen schema.

## Tiers and gates

| Tier | Scope |
|---|---|
| Tier 0 | End-to-end skeleton: ingestion → event_log → variant graph → ProcessView renders something real. |
| Tier 1 | Spend + waste: cost attribution, rate card, waste detectors, SpendView + WasteView. |
| Tier 2 | Forecaster, simulator, capability index, SimulatorView, narrator. |
| Tier 3 | Polish / stretch — only if Tier 0–2 are done and stable. |

| Gate | Hour | Rule |
|---|---|---|
| Gate 1 | Hour 8 | Tier 0 rendering end to end, or drop Tier-2 work immediately. |
| Gate 2 | Hour 20 | Simulator running end to end, or everyone moves onto it. |
| Gate 3 | Hour 28 | Feature freeze — anything not working gets removed from the demo, not fixed. |

## Ownership

| Path | Owner |
|---|---|
| `backend/app/ingestion/`, `backend/app/synthetic/` | Nishant |
| `backend/app/cost/`, `backend/app/waste/` | Diljit |
| `backend/app/models/` | Dipen |
| `frontend/` | Livana |
| `backend/app/api/`, `backend/app/db/`, `infra/`, `scripts/`, `docs/schema.sql` | Shared |

## Prerequisites

**Recommended: Docker.**
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) with
  Compose v2. On Windows, use the **WSL2 backend** (Docker Desktop enables
  this by default on modern installs — check Settings → General → "Use the
  WSL 2 based engine").

**Fallback: native toolchain.**
- Python 3.11+
- Node 20+
- PostgreSQL 15 (or Docker just for the `postgres` service — see below)

## Quickstart (Docker, same command on both OSes)

```bash
docker compose -f infra/docker-compose.yml up
```

This starts Postgres (schema applied automatically from
`docs/schema.sql` on first boot), the backend on
[http://localhost:8000](http://localhost:8000), and the frontend on
[http://localhost:5173](http://localhost:5173).

## Manual setup

Copy `.env.example` to `.env` first and review it either way.

### Linux / macOS

```bash
./scripts/setup.sh
```

This starts Postgres via Docker (even in the native path, to skip a local
Postgres install), waits for it to be healthy, applies `docs/schema.sql`,
creates a `backend/.venv` and installs backend deps, and runs `npm install`
in `frontend/`.

### Windows (PowerShell)

```powershell
.\scripts\setup.ps1
```

Functionally identical to `setup.sh`.

## Environment variables

See [`.env.example`](.env.example) for the full, current list. Summary:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `GITHUB_TOKEN` | Optional; raises GitHub API rate limit for ingestion |
| `GITHUB_REPOS` | Real repos to ingest (`apache/kafka,apache/cassandra`) |
| `ASF_JIRA_BASE_URL` | Apache Jira REST base URL (anonymous, no token) |
| `ASF_JIRA_PROJECTS` | Real Jira projects to ingest (`KAFKA,CASSANDRA`) |
| `PSEUDONYMIZATION_SALT` | Salt for `actor_hash = sha256(login + salt)` |
| `K_ANONYMITY_FLOOR` / `K_ANONYMITY_FALLBACK` | Privacy floor enforced at the query layer |
| `SESSION_GAP_MINUTES` / `SESSION_LEAD_IN_MINUTES` | Session-inference constants for cost attribution |
| `VITE_API_BASE_URL` | Frontend's backend base URL |

## Running ingestion

Real data (GitHub + Jira) and synthetic data (calendar + tokens) are
separate commands — never conflate them:

```bash
# Real GitHub + Jira pull
backend/.venv/bin/python -m app.ingestion.github_connector
backend/.venv/bin/python -m app.ingestion.jira_connector

# Regenerate synthetic calendar + token data
backend/.venv/bin/python -m app.synthetic.gen_calendar
backend/.venv/bin/python -m app.synthetic.gen_tokens
```

Or, inside Claude Code, use `/seed-synthetic` for the synthetic half.

## Running the app

**Docker:** `docker compose -f infra/docker-compose.yml up` runs both.

**Manual:**

```bash
# backend
cd backend && ../backend/.venv/bin/uvicorn app.main:app --reload

# frontend (separate terminal)
cd frontend && npm run dev
```

## Running tests

```bash
# backend
cd backend && .venv/bin/pytest

# frontend
cd frontend && npm test
```

## Troubleshooting

- **Windows line-ending issues (CRLF in `.sh`/`.py` files):** this repo
  ships a [`.gitattributes`](.gitattributes) forcing LF on `*.sh` and
  `*.py`. If you still see `\r` errors, run
  `git config core.autocrlf false` and re-clone, or `git add --renormalize .`.
- **Docker Desktop / WSL2:** on Windows, Compose needs the WSL2 backend
  enabled in Docker Desktop settings; without it, volume mounts and
  networking between services can behave inconsistently.
- **Postgres port 5432 already in use:** stop any local Postgres service,
  or change the host-side port mapping in `infra/docker-compose.yml`
  (`"5432:5432"` → e.g. `"5433:5432"`) and update `DATABASE_URL`
  accordingly.

## Check gate status

Inside Claude Code, run `/check-gate` for a report on tier completion
against the three gate hours above.
