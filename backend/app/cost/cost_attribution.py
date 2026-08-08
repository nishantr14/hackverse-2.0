"""
Cost attribution — joins inferred sessions + rate card into cost_event rows.
Owner: Diljit (cost lane).
Phase: Tier 1.

Determinism discipline: this is where a dollar figure is actually computed.
Every number the API/UI shows must trace back here (or to another module
like it), never to an LLM call.
"""

# TODO: hours (session_inference) * rate_band (rate_card) -> cost_event
# rows per docs/schema.sql, basis='session_inferred'.
