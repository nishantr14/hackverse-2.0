---
description: Adversarial audit of the repo against the privacy rules
---

Audit the repo against the privacy rules in `.claude/CLAUDE.md`. Report
findings by severity with file and line. Do not change code.

Be adversarial, not reassuring. This check backs our answer to "isn't this
employee surveillance", so a judge will be doing the same thing.

- Any code path where a GitHub login, email or real name could reach the
  analytics database, an API response, a log line, a test fixture, or a
  committed file.
- Any API route that could return a per-actor row for cost, capability or AI
  usage at any aggregation.
- Any query reaching a base table instead of a k-floored view.
- Any place `data/identity.db` is opened outside `backend/app/ingestion/`.
- Any grant of `v_actor_component_activity` or `event_log` to the app role.
- Any secret, token or `.env` value committed to the repo.
- Any UI string presenting synthetic data (meetings, AI tokens) as observed, or
  an inferred band as a stated one.
