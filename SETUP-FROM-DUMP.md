# Running the demo from a database dump

Ingestion takes ~40 minutes, needs a GitHub token, and hits the ASF's
servers. Nobody else on the team should do it. Restore the dump instead —
it is the same database, byte for byte, and every step below is verified.

The dump is **not in git** (20 MB, and a database does not belong in a
repo). Nishant sends it directly.

---

## What is in the dump, and why it is safe to pass around

It is the analytics database: 122,888 raw payloads, 136,201 events, 111,146
cost rows. It contains **no logins, no emails, no names, no salaries** —
contributors exist only as 16-character `actor_hash` values, and the
login→hash mapping never enters Postgres at all. Verified on this dump:

- identity-named columns in the schema: **none**
- email-shaped strings in a 20,000-row sample of `raw_payload`: **0**
- every `actor_hash`: exactly 16 hex characters

That separation is the whole privacy design, and it is what makes the dump
shareable.

### Two files that must never be sent

| file | why |
|---|---|
| `data/identity.db` | The ONLY place a login sits next to its hash. Sending it undoes the pseudonymisation for everyone. Gitignored, and blocked in `.claude/settings.json`. |
| `.env` | Contains a live GitHub personal access token. Use `.env.example`. |

---

## Setup

### 1. Repo

```bash
git clone https://github.com/nishantr14/hackverse-2.0.git
cd hackverse-2.0
git checkout nishant/esi-working-demo
```

### 2. Environment

```bash
cp .env.example .env
```

Leave `PSEUDONYMIZATION_SALT=change-me`. **Do not set a salt.** The hashes in
the dump were built with the shipped default; changing it makes your hashes
disagree with everyone else's, and it cannot be re-mapped — logins are
hashed before they are written, so there is nothing on disk to re-hash from.

If something already owns port 5432 (a native Postgres service is the usual
culprit), create `infra/docker-compose.override.yml`:

```yaml
services:
  postgres:
    ports: !override
      - "127.0.0.1:5433:5432"
```

`!override` is load-bearing — Compose *merges* `ports` lists, so without it
you publish 5432 *and* 5433 and the container refuses to start on the very
port you were avoiding. Then point `DATABASE_URL` and
`DATABASE_URL_READONLY` in `.env` at 5433.

### 3. Postgres

```bash
docker compose -f infra/docker-compose.yml up -d postgres
```

First boot runs `docs/schema.sql`, which creates the tables, the views, and
the read-only `esi_app` role.

### 4. Restore the data

Copy `esi-demo-data-2026-08-09.dump` next to the repo, then:

```bash
docker cp esi-demo-data-2026-08-09.dump infra-postgres-1:/tmp/esi.dump
docker exec infra-postgres-1 psql -U esi -d esi -c "TRUNCATE run_config;"
docker exec infra-postgres-1 pg_restore -U esi -d esi --data-only --disable-triggers /tmp/esi.dump
```

The `TRUNCATE` is not optional. `schema.sql` seeds `run_config` with 11 rows
and the dump carries 12 — including `history_cutoff`, which the pipeline
computes. Without it the restore stops on a duplicate key and you silently
keep the wrong window.

Data-only is deliberate: the schema and the role grants already exist from
step 3, so only rows are loaded.

### 5. Post-freeze views

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -e .   # .venv\Scripts\pip on Windows
PYTHONPATH=. .venv/bin/python -m app.db.migrate
```

**Do not skip this.** `docs/schema.sql` is frozen, so everything added after
it — the canonical event log, the append-only triggers, and every
process/waste/spend/simulate view — lives in `backend/migrations/` and is
applied only by this command. Skip it and you get a database that looks
completely healthy while seven API routes return 500 against views that were
never created.

It is idempotent. Run it whenever you pull.

### 6. Run it

```bash
cd backend && PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --port 8000
cd frontend && npm install && npm run dev
```

Open http://localhost:5173.

---

## Check it worked

```bash
curl -s localhost:8000/spend/summary
```

Expected: `totalCost` ≈ **84,777,933** (₹8.48 crore).

```bash
docker exec infra-postgres-1 psql -U esi -d esi -c "
SELECT 'raw_payload' t, count(*) FROM raw_payload
UNION ALL SELECT 'event_log', count(*) FROM event_log
UNION ALL SELECT 'cost_event', count(*) FROM cost_event
UNION ALL SELECT 'work_item', count(*) FROM work_item
UNION ALL SELECT 'actor', count(*) FROM actor;"
```

| table | rows |
|---|---|
| `raw_payload` | 122,888 |
| `event_log` | 136,201 |
| `cost_event` | 111,146 |
| `work_item` | 5,400 |
| `actor` | 1,725 |

Backend tests: `PYTHONPATH=. .venv/bin/python -m pytest -q` → 664 passed.

---

## If a route returns 500

Almost always step 5. Check the views exist:

```bash
docker exec infra-postgres-1 psql -U esi -d esi -c "\dv v_edges_by_variant"
```

Nothing back means migrations were never applied. Re-run
`python -m app.db.migrate`.

---

## Never run these

- `docker compose down -v` — deletes the `infra_pgdata` volume and the whole
  database with it.
- Any connector (`app.ingestion.*`) — ingestion is Nishant's lane. Re-fetching
  costs 40 minutes and hits Apache's infrastructure for data you already have.
- `pg_restore` without `--data-only` against a database that already has the
  schema — it will fight every existing object.
