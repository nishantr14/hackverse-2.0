# Engineering Spend Intelligence

HackVerse 2.0 (IBM × Celonis × 1M1B, "AI for Business Transformation"
track, 27 teams). **One sentence:** process mining for the software
development lifecycle — a priced unit of work, joining GitHub activity,
real Jira data, synthetic calendar/meeting time, and role-band rates into
one event log, then computing cost, waste, and a counterfactual "what-if"
simulator on top of it.

Any Claude Code session opened anywhere in this repo, by any teammate,
should start from this file's context instead of re-deriving it.

## Data sources — real vs synthetic

| Source | Status | Detail |
|---|---|---|
| GitHub (`apache/kafka`, `apache/cassandra`) | **Real** | REST/GraphQL + Actions API — commits, PRs, reviews, CI runs. |
| Apache Jira (`KAFKA`, `CASSANDRA` projects) | **Real** | `issues.apache.org/jira`, read anonymously via REST — no auth token needed for GET/search. |
| Jira↔commit linkage | **Real, exact** | Apache's own commit convention prefixes commits with the ticket key (e.g. `KAFKA-16234: ...`). This is exact string extraction — **do not build a fuzzy-match fallback**, it isn't needed. |
| Calendar / meeting time | **Synthetic** | Generated in `backend/app/synthetic/gen_calendar.py`. |
| AI token usage | **Synthetic** | Generated in `backend/app/synthetic/gen_tokens.py`, unless swapped for a real IBM Bob usage export — same schema either way. |

Never present synthetic data as real in the UI or narration. Never build
fuzzy matching for the Jira link — it's already exact.

## Determinism discipline

> Every number a human sees comes from SQL/pandas or a documented model.
> AI (a narrator LLM call) only explains numbers that already exist
> elsewhere — it never computes one.

This applies everywhere: API responses, frontend views, and any narration
text. `backend/app/narrate/narrator.py` is the *only* place an LLM call is
allowed to touch output the user sees, and it must receive pre-computed
figures, never raw data it summarizes into a number itself.

## Privacy rules

> Privacy by design, from the first commit ingested, not retrofitted.

- Pseudonymize actor identity at the ingestion loader: `sha256(login + salt)`
  (`backend/app/ingestion/pseudonymize.py`).
- The login→hash mapping table lives **physically outside** the analytics
  DB — never in the same Postgres instance as `event_log`/`cost_event`.
- Enforce a k-anonymity floor (default k=5, fallback k=3) at the query
  layer. Print it on screen when it triggers.
- Never build a per-person view for AI usage or any other metric.
- The API uses a **read-only** Postgres role, separate from the
  ingestion/loader's write role (`backend/app/db/session.py`).

## Schema — frozen

**Requires all four teammates present to change.** This is the contract
every workstream builds against before real rows exist. Canonical copy:
[`docs/schema.sql`](../docs/schema.sql).

```sql
actor(actor_hash PK, role_band, tenure_bucket, first_seen)
-- no name, no email, no salary. mapping table lives outside this DB.

work_item(work_item_id PK, repo, component, epic, opened_at, closed_at, source_ref)

event_log(
  event_id PK, work_item_id FK, actor_hash FK,
  activity,        -- commit | review_requested | review | changes_requested
                    -- | merge | ci_run | deploy
  ts, duration_s,   -- duration inferred, not measured
  attrs JSONB
)

cost_event(event_id FK, hours NUMERIC, rate_band NUMERIC, cost NUMERIC, basis TEXT)
-- basis: 'session_inferred' | 'ci_runner' | 'ai_tokens' | 'meeting'

rate_card(role_band PK, hourly NUMERIC, source TEXT)
-- source is a public citation string, rendered in the UI

ci_run(run_id PK, work_item_id FK, ts, runner_minutes, conclusion)
ai_usage(usage_id PK, work_item_id FK, ts, vendor, tokens_in, tokens_out, cost)

variant(variant_id PK, repo, activity_sequence TEXT[], n_cases, total_cost)
```

## Tiers and gates

Three hard gates. At each one, the rule is binary — no partial credit.

| Tier | Scope |
|---|---|
| Tier 0 | End-to-end skeleton: ingestion → event_log → variant graph → ProcessView renders something real. |
| Tier 1 | Spend + waste: cost attribution, rate card, all four waste detectors, SpendView + WasteView. |
| Tier 2 | Forecaster, simulator, capability index, SimulatorView, narrator. |
| Tier 3 | Polish / stretch — only if Tier 0–2 are done and stable. |

| Gate | Hour | Rule |
|---|---|---|
| Gate 1 | Hour 8 | Tier 0 rendering end to end, or **drop Tier-2 work immediately**. |
| Gate 2 | Hour 20 | Simulator running end to end, or **everyone moves onto it**. |
| Gate 3 | Hour 28 | Feature freeze — anything not working gets **removed from the demo, not fixed**. |

`ProcessView` leads the Round 2 demo. If time runs short, protect
`SimulatorView` above all other frontend work.

## Who owns what

| Path | Owner | Lane |
|---|---|---|
| `backend/app/ingestion/` | Nishant | GitHub + Jira connectors, pseudonymization |
| `backend/app/synthetic/` | Nishant | Calendar + token generators |
| `backend/app/cost/` | Diljit | Session inference, cost attribution, rate card |
| `backend/app/waste/` | Diljit | Rework, review latency, CI waste, key-person risk |
| `backend/app/models/` | Dipen | Forecaster, capability index, simulator |
| `backend/app/narrate/` | Tier 2, whoever picks it up | Narrator LLM call — explains only, never computes |
| `backend/app/api/` | Shared | Routers over the above; keep thin, no business logic |
| `backend/app/db/` | Shared infra | SQLAlchemy models (must match `docs/schema.sql` exactly), sessions |
| `frontend/` | Livana | All views, components, API wrappers |
| `infra/`, `scripts/`, `docs/schema.sql`, `.env.example` | Shared infra | Whoever touches an env var updates `.env.example` and the README table too |

## Running locally

See [`README.md`](../README.md) for the full quickstart, manual setup
(Linux/macOS vs Windows), environment variables, and troubleshooting.
Short version: `docker compose -f infra/docker-compose.yml up` is the one
command that works identically on both OSes.

## Slash commands

- `/seed-synthetic` — regenerate calendar + token synthetic data.
- `/check-gate` — report current tier status against the three gate hours above.
