---
description: Regenerate synthetic calendar + AI token usage data
---

Regenerate the synthetic (non-real) portions of the dataset:

1. Run `backend/app/synthetic/gen_calendar.py` to regenerate meeting/calendar
   events.
2. Run `backend/app/synthetic/gen_tokens.py` to regenerate AI token usage
   (`ai_usage` rows).
3. Confirm the generated rows join cleanly against existing `work_item` /
   `actor` rows in the DB — report any orphaned foreign keys.
4. Remind the user this data is synthetic and must never be presented as
   real GitHub/Jira data in the UI or narration (see the data-sources table
   in `.claude/CLAUDE.md`).

Do not touch `backend/app/ingestion/` (real GitHub/Jira connectors) as part
of this command — that's a separate, real-data path.
