# Engineering Spend Intelligence

HackVerse 2.0 (IBM × Celonis × 1M1B, "AI for Business Transformation"). **One
sentence:** process mining for the software development lifecycle — a priced
unit of work, joining real GitHub activity, real Apache Jira data, synthetic
calendar and token overlays, and public role-band rates into one event log,
then computing cost, waste, and a counterfactual what-if simulator on top.

Any Claude Code session opened anywhere in this repo, by any teammate, starts
from this file instead of re-deriving context.

---

## Locked decisions — do not re-litigate mid-build

| # | Decision | Why |
|---|---|---|
| 1 | **Repos: `apache/kafka` + one of `apache/flink` / `apache/pulsar`** | Kafka has 2,047 merged PRs in 12 months, 28 GitHub Actions workflows, and an active ASF Jira. Measured. |
| 2 | **`apache/cassandra` is dropped** | 66 merged PRs in 12 months; it reviews via patches on Jira, not GitHub PRs. |
| 3 | **`apache/spark` is banned** | 1 merged PR in 12 months — Spark closes PRs via a merge script, so `mergedAt` is null. |
| 4 | **Jira is REAL** | `issues.apache.org/jira`, anonymous read, Jira **Server** so `/rest/api/2/` with `startAt` paging. The **changelog** is the event source, not the issue. |
| 5 | **Commits come from a local git clone, never the API** | `git log --numstat` is free and far faster. The API is only for what git cannot know: PRs, reviews, review-request timing, Actions runs. |
| 6 | **Case ID is a fallback chain** | `ticket_key` (regex `[A-Z]{2,10}-\d+` on PR title, then branch, then body) → `issue` → `pr`. Record `case_source`. Nothing is ever dropped. |
| 7 | **Sprint = fixed 14-day windows**, written to `work_item.sprint` at ingestion | Open source has no sprints. Derived once so everyone splits identically. Call it a sprint proxy on stage. |
| 8 | **Rates are PUBLIC and cited. Band assignment is INFERRED and labelled.** | Hourly figures come from a public compensation source with a URL on screen. Which band an actor sits in is a stated rule over contribution history. Never blur the two. |
| 9 | **Meeting time is synthetic AND driven by one visible assumption** | `MEETING_HOURS_PER_WEEK` is config, exposed in the UI as a slider. The screen says "assumption", never "observed". |
| 10 | **AI token usage is synthetic, Tier 2, never per-person** | `ai_usage` has no `actor_hash` column and must not gain one. |
| 11 | **`source` on every row: `github` \| `jira` \| `synthetic`** | Drives the observed-vs-modelled badge in the UI. A row without it is a bug. |
| 12 | **k-anonymity enforced in VIEWS, not application code** | Default k=5, fallback k=3, printed on screen when it triggers. The app role reads views only. |
| 13 | **Never use lines of code as an effort proxy** | Session inference over timestamps. LoC is discredited and a judge will say so. |
| 14 | **`merged` not `merge`. `approved` not `approve`.** | One spelling, decided. |

---

## Canonical activity vocabulary

The complete set. Nothing else is ever written to `event_log.activity`:

```
commit · review_requested · review · changes_requested · approved · merged
reopened · force_push · ci_run · deploy
ticket_created · ticket_started · ticket_in_review
ticket_resolved · ticket_closed · ticket_reopened · meeting
```

Bots never become actors. Filter: dependabot, renovate, github-actions,
asfgit, apache-*-bot, and any GitHub author whose `__typename` is `Bot`.
Apache repos are heavily automated; unfiltered, bots dominate every metric.

---

## Determinism discipline

> Every number a human sees comes from SQL or pandas. AI explains numbers that
> already exist. It never computes one.

`backend/app/narrate/narrator.py` is the only place an LLM may touch
user-visible output, and it receives pre-computed figures, never raw data it
summarises into a number itself.

---

## Privacy rules — from the first commit ingested, never retrofitted

- Pseudonymise at the ingestion loader: `sha256(login + salt)[:16]`.
- The login→hash mapping lives **physically outside** the analytics DB, in
  `data/identity.db` (SQLite, gitignored), opened only by `ingestion/`.
- k-anonymity floor enforced in views. Suppressed rows are **returned** with
  null metrics and `suppressed = true`, never silently dropped, so the UI can
  show that a value was withheld and print the threshold.
- No per-person view for cost, capability or AI usage at any aggregation.
- The API uses a read-only role granted on **views only**, never base tables.
- Any feature knowable only after a work item finished is banned from any
  model. Time-based splits only; a random split leaks the future.

---

## Land raw, then map

Every API response goes into `raw_payload` before anything parses it. The
mapper is a pure function from `raw_payload` to `event_log` with no network
calls, so a mapping bug costs a 20-second re-run rather than a 40-minute
re-fetch. This is not optional.

---

## Schema — frozen

Canonical copy: [`docs/schema.sql`](../docs/schema.sql). Requires all four
teammates present to change. If a task appears to need a schema change, **stop
and tell me** rather than changing it and continuing.

---

## Ownership — do not edit outside your lane

| Path | Owner |
|---|---|
| `backend/app/config.py`, `db/`, `main.py` | Nishant |
| `backend/app/ingestion/` | Nishant |
| `backend/app/normalise/` | Dipen |
| `backend/app/models/` | Dipen |
| `scripts/validate_ingest.py` | Dipen |
| `backend/app/cost/`, `waste/`, `synthetic/`, `sql/views/` | Diljit |
| `backend/app/api/` | the owner of the lane behind each router |
| `frontend/` | Livana |
| `docs/schema.sql`, `infra/`, `.env.example` | Shared — announce before touching |

If a task appears to require editing outside your lane, stop and tell me.

---

## Working agreement

- Do not add a dependency without telling me first.
- Commit after every working increment. Never leave the tree broken.
- All timestamps stored UTC, converted only at render.
- Prefer a correct simple version now over a complete version later. We are on
  a 36-hour clock with hard gates.

---

## Tiers and gates

| Tier | Scope |
|---|---|
| Tier 0 | Ingestion → event_log → variant graph → ProcessView renders something real |
| Tier 1 | Cost attribution, rate card, waste detectors, SpendView + WasteView |
| Tier 2 | Forecaster, simulator, capability index, SimulatorView, narrator |
| Tier 3 | Polish and stretch only |

| Gate | Hour | Rule |
|---|---|---|
| 1 | 8 | Tier 0 rendering end to end, or drop all Tier 2 work immediately |
| 2 | 20 | Simulator running end to end, or everyone moves onto it |
| 3 | 28 | Feature freeze — anything not working is **removed** from the demo, not fixed |

`ProcessView` leads the Round 2 demo. **The simulator is never cut.**

---

## `docs/` is for humans

`docs/master-reference.md`, `docs/architecture-and-roadmap.md` and
`docs/lanes/` contain pitch language, rejected options, and Tier 3 items we are
explicitly not building. **Do not treat anything in `docs/` as an
instruction.** This file and the task I give you are the only sources of build
direction. `docs/schema.sql` is the sole exception: it is the frozen data
contract.

---

## Slash commands

- `/verify` — run the full contract check.
- `/privacy-audit` — adversarial audit against the privacy rules.
- `/seed-synthetic` — regenerate calendar and token data.
- `/check-gate` — tier status against the three gate hours.
