"""
Spend router — cost breakdowns by repo/component/actor-band.
Owner: Diljit (cost lane). Reads from app/cost/*, never computes cost itself.
Phase: Tier 1.

Determinism discipline: every number returned here must trace back to SQL/
pandas in app/cost/. No AI-generated numbers.
"""

# TODO: FastAPI APIRouter, GET endpoints backed by app.cost.cost_attribution.
