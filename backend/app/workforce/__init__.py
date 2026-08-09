"""
The workforce layer — the one place in this product that names people.

SEPARATE FROM THE ANALYTICS LAYER, AND THE SEPARATION IS STRUCTURAL.

Everything under `app/normalise`, `app/cost` and `app/models` is derived from
the event log: observed telemetry, pseudonymised at ingestion, never
attributable to a person. Nothing in this package is. This layer holds what an
employee VOLUNTEERED — a preference form they filled in and a resume they
supplied — which is a different consent basis and therefore a different rule.

The two are never joined. `employee_id` has nothing to do with `actor_hash`,
there is no type in this package carrying both, and the profiles do not even
live in the analytics database — see `store.py`. That last part is deliberate:
"never joined" enforced by two different databases is a fact, whereas "never
joined" enforced by everyone remembering not to is a convention.

WHAT THIS PACKAGE MAY NOT DO
    - It may not read the event log, cost tables, or capability index.
    - It may not put a productivity figure on a named card. Cycle time,
      throughput, review counts and items merged are all forbidden in a
      recommendation payload; `test_workforce.py` asserts their absence.
    - It may not assign anybody. It produces a ranked recommendation that a
      human reviews.
"""
