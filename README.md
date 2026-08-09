# Engineering Spend Intelligence

Process mining for the software development lifecycle: a priced unit of
work, joining GitHub activity, real Apache Jira data, synthetic
calendar/meeting time, and role-band rates into one event log, then
computing cost, waste, and a counterfactual "what-if" simulator on top of
it. Built for HackVerse 2.0 (IBM × Celonis × 1M1B, "AI for Business
Transformation" track).

Data ingested from two real repos and their real Jira projects:
`apache/kafka` (KAFKA) and `apache/flink` (FLINK). Calendar/meeting
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

Canonical copy of this table lives in
[`.claude/CLAUDE.md`](.claude/CLAUDE.md). If they disagree, that file wins.

| Path | Owner |
|---|---|
| `backend/app/config.py`, `db/`, `main.py`, `ingestion/` | Nishant |
| `backend/app/normalise/`, `models/`, `scripts/validate_ingest.py` | Dipen |
| `backend/app/cost/`, `waste/`, `synthetic/`, `sql/views/` | Diljit |
| `backend/app/api/` | the owner of the lane behind each router |
| `frontend/` | Livana |
| `docs/schema.sql`, `infra/`, `.env.example` | Shared — announce before touching |

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

Every key in `.env.example` has exactly one field on `Settings` in
`backend/app/config.py`, and a test fails if the two drift apart. Add a key in
both places or neither.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres, **write** role `esi` — ingestion and synthetic generators only |
| `DATABASE_URL_READONLY` | Postgres, **read** role `esi_app` — the API. SELECT on views only |
| `GITHUB_TOKEN` | Optional; raises GitHub API rate limit 60/hr → 5000/hr |
| `GITHUB_REPOS` | Real repos to ingest (`apache/kafka,apache/flink`) |
| `HISTORY_MONTHS` | How far back ingestion reaches |
| `ASF_JIRA_BASE_URL` | Apache Jira REST base URL (anonymous, no token) |
| `ASF_JIRA_PROJECTS` | Real Jira projects to ingest (`KAFKA,FLINK`) |
| `PSEUDONYMIZATION_SALT` | Salt for `actor_hash = sha256(login + salt)[:16]` |
| `IDENTITY_DB_PATH` | SQLite login→hash map. Outside Postgres, gitignored, ingestion-only |
| `K_ANONYMITY_FLOOR` / `K_ANONYMITY_FALLBACK` | Privacy floor, enforced in views |
| `SESSION_GAP_MINUTES` / `SESSION_LEAD_IN_MINUTES` / `SESSION_DAILY_CAP_HOURS` | Session-inference constants |
| `SPRINT_DAYS` | Fixed-window sprint **proxy**; open source has no sprints |
| `MEETING_HOURS_PER_WEEK` | An **assumption**, surfaced in the UI as a slider — never observed data |
| `DATA_SOURCE` | `real` or `fixtures`; badges the dataset in the UI |
| `VITE_API_BASE_URL` | Frontend's backend base URL |
| `ALLOWED_ORIGINS` | Origins the API accepts CORS requests from (comma-separated) |

## Running ingestion

Real data (GitHub + Jira) and synthetic data (calendar + tokens) are
separate commands — never conflate them:

Run these from `backend/`. On Windows the interpreter is
`.venv\Scripts\python.exe`; on Linux/macOS it is `.venv/bin/python`.

```bash
# Real GitHub + Jira pull
.venv/bin/python -m app.ingestion.github_connector
.venv/bin/python -m app.ingestion.jira_connector

# Regenerate synthetic calendar + token data
.venv/bin/python -m app.synthetic.gen_calendar
.venv/bin/python -m app.synthetic.gen_tokens
```

Ingestion uses the **write** role and opens `data/identity.db`. Nothing
outside `backend/app/ingestion/` may do either — a test enforces it.

Or, inside Claude Code, use `/seed-synthetic` for the synthetic half.

## Building the canonical event log

Ingestion lands payloads. This turns them into the one event log everything
else reads. It touches no network, so it is safe to re-run as often as you
like — every event id is a hash of its own evidence, so a second pass upserts
in place rather than doubling the log.

```bash
.venv/bin/python -m app.normalise.event_log
```

It applies `migrations/002_canonical_event_log.sql` itself, then prints the
fourteen-item verification report. `--verify` prints the report without
rebuilding.

**Read `v_event_log`, not the base tables.** It is the log in the shape a
process-mining tool expects, with the case attributes already joined on:

| column | meaning |
|---|---|
| `case_id` | the work item — a Jira key, else `owner/name#pr`, else `owner/name@sha12` |
| `activity` | one of the seventeen canonical activities, nothing else |
| `ts` | the original source timestamp, UTC. Author date for a commit, never the commit date; `createdAt` for a PR event, never `updatedAt` |
| `resource` | `actor_hash`. NULL for CI and for bots, always with `attrs.actor_absent` saying which |
| `repo`, `component`, `sprint`, `case_source`, `jira_key` | case attributes |
| `ingest_source` | `git_local` / `github_graphql` / `github_actions` / `asf_jira` |
| `in_window` | false for events retained from before `HISTORY_MONTHS` to complete a case history |
| `step` | 1..n, ordered by `(ts, event_id)` — a deterministic case sequence |

`v_case_evidence` answers "which sources proved something about this case"
(`has_git`, `has_pr`, `has_ci`, `has_jira`) so nobody re-derives that join
four times and gets four different numbers. `v_case_sequence` — the variant
key — is in the frozen schema and unchanged.

Unattributable CI runs keep a NULL `work_item_id` in `ci_run` and are counted
in the report. They cannot appear in `event_log`, whose `work_item_id` is NOT
NULL.

## Full pipeline, in order

Everything below runs against `raw_payload` and touches no network, so any
step can be re-run freely. Every one is idempotent — re-running upserts in
place rather than doubling.

```bash
python -m app.db.migrate              # 1. views + triggers  (REQUIRED FIRST)
python -m app.normalise.event_log     # 2. raw_payload -> event_log
python -m app.cost.rate_card --seed   # 3. public cited rates -> rate_card
python -m app.cost.band_inference     # 4. inferred bands -> actor.role_band
python -m app.cost.session_inference  # 5. clustered timestamps -> work_session
python -m app.synthetic.gen_tokens    # 6. modelled AI usage -> ai_usage
python -m app.synthetic.gen_calendar  # 7. modelled meetings -> calendar_event
python -m app.cost.cost_attribution   # 8. everything above -> cost_event
```

**Step 1 is not optional and is easy to miss.** Docker's initdb loads
`docs/schema.sql` and nothing else, so every view added after the freeze —
the canonical event log, the append-only triggers, and all the
process/waste/spend/simulate views — exists only in `backend/migrations/`.
Skip it and you get a database that looks completely healthy while seven API
routes return 500 against views that were never created. `deploy.sh` runs it
for you.

Steps 3 and 6 refuse to run if their citation in `config/rates.yaml` or
`config/ai_rates.yaml` is blank. That is deliberate: the source string is
rendered on screen next to the money, and an uncited figure is an invented
one. Fill the citation in rather than working around the refusal.

Order matters in two places: bands (4) must exist before sessions are priced
(8), and the synthetic overlays (6, 7) must exist before attribution (8) or
their cost rows are simply absent from the total.

## Running the app

**Docker:** `docker compose -f infra/docker-compose.yml up` runs both.

**Manual:**

```bash
# backend
cd backend && ../backend/.venv/bin/uvicorn app.main:app --reload

# frontend (separate terminal)
cd frontend && npm run dev
```

### Reachable from another machine (LAN/VPN, e.g. a 172.x address)

`localhost` means something different on every machine, so it never works
across a network — the frontend's API calls and the backend's CORS allowlist
both need the actual address.

**Docker:** set `HOST_IP` once; the compose file wires it into both sides:

```bash
HOST_IP=172.20.10.5 docker compose -f infra/docker-compose.yml up
```

Then browse to `http://172.20.10.5:5173` from any machine on that network.

**Manual:** both dev servers default to binding `localhost` only, and
`VITE_API_BASE_URL`/`ALLOWED_ORIGINS` need to name the real address:

```bash
# backend — bind all interfaces, not just localhost
cd backend && ALLOWED_ORIGINS="http://172.20.10.5:5173" \
  ../backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --reload

# frontend (separate terminal) — --host binds all interfaces
cd frontend && VITE_API_BASE_URL="http://172.20.10.5:8000" npm run dev -- --host
```

The host's firewall must also allow inbound `5173` and `8000` from that
network — this is an OS setting, not something either dev server controls.

## Running tests

```bash
cd backend && .venv/bin/pytest -q
```

On Windows: `cd backend; .venv\Scripts\pytest.exe -q`.

Most tests run with no database — including the model-vs-`schema.sql` drift
check, which is a static comparison against the SQL text. Tests that need
Postgres **skip** rather than fail when nothing is listening, so a skip count
above zero means "start the database", not "broken".

```bash
cd backend && .venv/bin/ruff check .
```

## Troubleshooting

- **Windows line-ending issues (CRLF in `.sh`/`.py` files):** this repo
  ships a [`.gitattributes`](.gitattributes) forcing LF on `*.sh` and
  `*.py`. If you still see `\r` errors, run
  `git config core.autocrlf false` and re-clone, or `git add --renormalize .`.
- **Docker Desktop / WSL2:** on Windows, Compose needs the WSL2 backend
  enabled in Docker Desktop settings; without it, volume mounts and
  networking between services can behave inconsistently.
- **Postgres port 5432 already in use** (a native Postgres service is the
  usual cause, and it fails as `password authentication failed for user
  "esi"` rather than as a port error, because the *other* server answers):
  create `infra/docker-compose.override.yml` — gitignored, local to you,
  loaded automatically by Compose:

  ```yaml
  services:
    postgres:
      ports:
        - "5433:5432"
  ```

  Then point `DATABASE_URL` and `DATABASE_URL_READONLY` in your `.env` at
  5433. Do not change `infra/docker-compose.yml` itself — it is shared.

- **Schema changes don't appear:** `docs/schema.sql` is applied by Postgres
  only on a *first* boot with an empty `pgdata` volume. After the schema
  changes you must recreate the volume, which **deletes all ingested data**:

  ```bash
  docker compose -f infra/docker-compose.yml down -v && docker compose -f infra/docker-compose.yml up -d postgres
  ```

- **`permission denied for table event_log` from the API:** working as
  designed. The API's role reads views only; `event_log` and `cost_event`
  are ungranted so the k-anonymity floor cannot be bypassed. Query a `v_*`
  view instead.

## Slash commands (inside Claude Code)

| Command | Purpose |
|---|---|
| `/verify` | Full contract check — schema, model drift, privacy, tests, lint |
| `/privacy-audit` | Adversarial audit against the privacy rules |
| `/seed-synthetic` | Regenerate calendar + AI token data |
| `/check-gate` | Tier status against the three gate hours |
