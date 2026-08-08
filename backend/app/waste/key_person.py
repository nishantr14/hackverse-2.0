"""
Key-person risk detector.
Owner: Diljit (waste lane).
Phase: Tier 2.

Flags components/work_items where activity concentrates on a small number
of actor_hash values. Must respect the k-anonymity floor (see
.claude/CLAUDE.md privacy rules) — never expose a per-person view.
"""

# TODO: compute activity concentration per component, enforce k-anonymity
# floor before returning any actor-level detail.
