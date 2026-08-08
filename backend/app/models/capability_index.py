"""
Capability index.
Owner: Dipen (models lane).
Phase: Tier 2.

Must respect the k-anonymity floor (see .claude/CLAUDE.md) — this index is
computed at the team/component level, never surfaced as a per-person score.
"""

# TODO: aggregate cost/waste/throughput signals into a capability index per
# component/team, enforcing k-anonymity before any grouped output.
