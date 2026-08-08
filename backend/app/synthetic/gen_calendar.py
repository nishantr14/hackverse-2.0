"""
Synthetic calendar/meeting time generator.
Owner: Nishant (ingestion lane).
Phase: Tier 1.

This data is SYNTHETIC, not real — never present it as real in the UI or
narration. Same schema as a real IBM Bob usage export would use, so it can
be swapped in later without a schema change. Regenerate via the
/seed-synthetic slash command (see .claude/commands/seed-synthetic.md).
"""

# TODO: generate meeting events per actor, mapped into event_log
# (activity values outside the commit/review/ci set) + cost_event rows
# with basis='meeting'.
