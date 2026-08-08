"""
Process router — variants and the cost-weighted process graph.
Owner: shared (ingestion + cost data, consumed by Livana's ProcessView —
the view that leads the Round 2 demo).
Phase: Tier 0 (this is the end-to-end path the hour-8 gate checks).

Reads variant table (see docs/schema.sql) computed from event_log.
"""

# TODO: FastAPI APIRouter, GET endpoint returning variant graph nodes/edges
# with per-edge cost weight.
