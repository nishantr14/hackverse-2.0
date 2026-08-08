"""
Counterfactual "what-if" simulator.
Owner: Dipen (models lane).
Phase: Tier 2. Hour-20 gate: must run end to end, or the whole team moves
onto it (see .claude/CLAUDE.md).

Deterministic given its inputs — calls forecaster.py for predictions but
does not introduce randomness or LLM calls of its own. Protect
SimulatorView (frontend) above all other UI if time runs short.
"""

# TODO: accept a scenario (e.g. "add N reviewers", "cut CI matrix"),
# recompute cost/waste deterministically via forecaster + cost/waste
# modules.
