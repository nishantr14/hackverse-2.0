"""
Simulate router — counterfactual "what-if" endpoint.
Owner: Dipen (models lane). Reads from app/models/simulator.py.
Phase: Tier 2. Hour-20 gate: this must run end to end, or everyone moves
onto it (see .claude/CLAUDE.md).

The simulator is deterministic and calls the forecaster — this router does
not do any modeling itself, only request/response shaping.
"""

# TODO: FastAPI APIRouter, POST endpoint accepting a scenario, returning
# app.models.simulator output.
