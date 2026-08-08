"""
Synthetic AI token usage generator.
Owner: Nishant (ingestion lane).
Phase: Tier 1.

This data is SYNTHETIC, not real — never present it as real in the UI or
narration. Same schema as a real IBM Bob usage export would use, so it can
be swapped in later without a schema change. Regenerate via the
/seed-synthetic slash command (see .claude/commands/seed-synthetic.md).
"""

# TODO: generate ai_usage rows per docs/schema.sql (vendor, tokens_in,
# tokens_out, cost) linked to work_item_id.
