"""
Waste router — rework, review latency, CI waste, key-person risk.
Owner: Diljit (waste lane). Reads from app/waste/*, never computes waste itself.
Phase: Tier 1.

k-anonymity floor (see .claude/CLAUDE.md privacy rules) must be enforced
here at the query layer before any grouped-by-actor result leaves the API.
"""

# TODO: FastAPI APIRouter, GET endpoints backed by app.waste.*.
