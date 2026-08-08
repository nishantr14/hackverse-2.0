---
description: Run the full contract check and report pass/fail per item
---

Run the full contract check and report pass/fail per item as a table. Do not fix
anything unless I ask — just report.

## 1. The data contract — run the script, do not re-derive it

```bash
python scripts/validate_ingest.py
```

Ten checks against the live database: row counts, commit-before-merge ordering,
actor referential integrity, the activity vocabulary, `source`, `sprint`,
identity columns, k-anon survival, review-latency coverage, trainable sprint
windows. Exit codes: `0` pass, `1` at least one FAILED, `2` nothing failed but
some checks could not run.

Report its table verbatim. **Do not paraphrase the K-ANON SURVIVAL block** —
reproduce the component counts and both thresholds as printed. If the script
exits non-zero, say which numbered checks failed and stop calling the run green.

A check reported as `WARN` is not a pass. The common case is check 2 going
vacuous because no `merged` events exist yet — that means the commit-to-PR
mapping is *unverified*, not *verified*.

If the script cannot reach Postgres, say so plainly and report the remaining
items; do not substitute your own SQL for its checks.

## 2. The code contract — the four things the script cannot see

11. `docs/schema.sql` applies cleanly to a fresh database.
12. Every SQLAlchemy model in `backend/app/db/models.py` matches
    `docs/schema.sql` column for column. List any drift.
13. `data/identity.db` is in `.gitignore` and is not tracked by git.
14. `pytest` and `ruff check` both pass:

```bash
cd backend && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check .
```

## 3. Summary

Finish with one line: how many of the ten data checks passed, how many of the
four code checks passed, and whether the ingest is safe to build views on.
