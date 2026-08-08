---
description: Run the full contract check and report pass/fail per item
---

Run the full contract check and report pass/fail per item as a table. Do not fix
anything unless I ask — just report.

1. `docs/schema.sql` applies cleanly to a fresh database.
2. Every SQLAlchemy model in `backend/app/db/models.py` matches
   `docs/schema.sql` column for column. List any drift.
3. No column named login, email, name or salary exists anywhere in the
   analytics database.
4. `data/identity.db` is in `.gitignore` and is not tracked by git.
5. Every row in `event_log` has a non-null `source`.
6. Every value in `event_log.activity` is in the canonical vocabulary in
   `.claude/CLAUDE.md`.
7. Every `work_item` has a non-null `sprint`.
8. No `merged` event precedes the first `commit` on any work item. (If this
   fails, the commit-to-PR mapping is wrong and everything downstream is wrong.)
9. `SELECT count(*) FROM v_spend_by_component WHERE NOT suppressed` — how many
   components survive the k floor? Print the list.
10. `pytest` passes.
11. `ruff check` passes.

Finish with a one-line summary: how many checks passed out of eleven.
