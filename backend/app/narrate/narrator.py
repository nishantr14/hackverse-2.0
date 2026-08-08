"""
Narrator — Granite/LLM call that explains numbers, never computes them.
Owner: whoever picks up Tier 2 narration work.
Phase: Tier 2 (explicitly last-priority — Tier 0/1 numbers must be real
and correct before this gets touched).

DETERMINISM DISCIPLINE (see .claude/CLAUDE.md): this module receives
already-computed numbers from cost/waste/models and produces natural-
language explanation only. It must never be the source of a number a human
sees on screen.
"""

# TODO: prompt template that takes pre-computed figures (spend, waste,
# variant, simulation delta) and returns explanatory text only.
