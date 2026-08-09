---
description: Regenerate synthetic calendar + AI token usage data
---

Regenerate the synthetic (non-real) portions of the dataset. Both generators
are seeded, deterministic and idempotent — re-running produces byte-identical
rows and changes no counts.

Run from `backend/` (on Windows the interpreter is `.venv\Scripts\python.exe`):

1. **Meetings.** Driven by one assumption, `MEETING_HOURS_PER_WEEK`, which the
   frontend exposes as a slider:

   ```
   python -m app.synthetic.gen_calendar --dry-run
   python -m app.synthetic.gen_calendar
   python -m app.synthetic.gen_calendar --meeting-hours 6   # try the slider
   ```

   Report the RECONCILIATION line. Mean hours per actor per week must land
   within 5% of the assumption; if it does not, the generator is wrong, not
   the check. Regenerating replaces the previous meetings rather than adding
   to them, so a different slider value gives a different answer everywhere
   downstream — that is the point of the slider being real.

2. **AI tokens.** Volumes are modelled, prices are cited:

   ```
   python -m app.synthetic.gen_tokens --dry-run
   python -m app.synthetic.gen_tokens
   ```

   It refuses to run until `config/ai_rates.yaml` has a publisher, URL,
   retrieval date and a USD→INR rate with its own source. Report the adoption
   curve by sprint and the share of work items with any AI usage, and check
   the `cost reconciles` line says YES — cost is computed from tokens and
   prices, never sampled, so the two cannot drift.

3. Confirm the generated rows join cleanly against existing `work_item` rows
   and report any orphaned foreign keys.

4. Re-run the verification so the observed-vs-synthetic share is current:

   ```
   python -m app.normalise.event_log --verify
   ```

5. Remind the user this data is synthetic and must never be presented as real
   GitHub/Jira data in the UI or narration (see `.claude/CLAUDE.md`).

**Privacy, non-negotiable:** `ai_usage` has no `actor_hash` column and must
not gain one. Do not join token spend to `actor`, and do not create a view
exposing it per person at any aggregation. `calendar_event` has no title, no
description and no attendee list; that absence is the guarantee, so do not add
them.

Do not touch `backend/app/ingestion/` (real GitHub/Jira connectors) as part of
this command — that is a separate, real-data path.
