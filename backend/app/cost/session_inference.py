"""
Session inference — time-clustered effort model.
Owner: Diljit (cost lane).
Phase: Tier 1.

Infers work sessions (and therefore hours) by clustering event_log
timestamps per actor/work_item, NOT by counting lines of code. LoC is not
a cost signal here and must not be used as one.
"""

# TODO: cluster event_log rows by actor_hash + time-gap threshold into
# inferred sessions, output hours per session for cost_attribution.
