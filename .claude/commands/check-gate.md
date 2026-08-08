---
description: Report current tier status against the three hard gates
---

Report where the project stands against the tier/gate table in
`.claude/CLAUDE.md`:

1. Read the tier table (Tier 0–3) and gate table (hour 8 / 20 / 28) from
   `.claude/CLAUDE.md`.
2. For each tier, check the repo for evidence of completion — e.g. for
   Tier 0: does `backend/app/ingestion/` produce real `event_log` rows, does
   `backend/app/api/process.py` return real data, does
   `frontend/src/views/ProcessView.tsx` render it end to end (not just a
   stub)? Look past "file exists" — check whether TODOs remain.
3. Compare the current wall-clock time (if known) or ask the user what hour
   of the hackathon it is against the three gate hours.
4. Report a short punch list: what's done, what's missing, and — if a gate
   has passed without its condition met — say explicitly what the rule
   requires (drop Tier-2 work / everyone moves onto the simulator / freeze
   and remove broken features from the demo).

Be blunt about gate failures. The gates are binary by design — don't soften
"not done" into "almost done."
