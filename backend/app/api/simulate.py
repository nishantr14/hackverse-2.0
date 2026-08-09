"""
Simulate router — counterfactual "what-if" endpoint.
Owner: Dipen (models lane). Reads from app/models/simulator.py.
Phase: Tier 2. Hour-20 gate: this must run end to end, or everyone moves
onto it (see .claude/CLAUDE.md).

The simulator is deterministic — this router does no modelling itself, only
request/response shaping. A refused scenario comes back as a 422 with the
reason in plain language, because "we cannot answer that honestly" is a
better demo than a confident zero.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_read_session
from app.models.simulator import (
    RAMP_EFFICIENCY,
    RAMP_WEEKS,
    Scenario,
    ScenarioRefused,
    list_components,
    load_component,
    run,
)

router = APIRouter(prefix="/simulate", tags=["simulate"])


class ScenarioRequest(BaseModel):
    """Additive. The three original fields are unchanged and still required,
    so every existing caller keeps working and gets the response it always
    got — the named block only appears when `namedRecommendations` is set."""

    source_project: str = Field(alias="sourceProject")
    dest_project: str = Field(alias="destProject")
    engineer_count: int = Field(alias="engineerCount", ge=1, le=200)

    #: Opt in to the workforce layer. Default False keeps capacity mode — the
    #: anonymous simulator — as the behaviour of an unchanged request.
    named_recommendations: bool = Field(
        default=False, alias="namedRecommendations"
    )
    shift: str = Field(default="flexible", alias="shift")
    availability: list[str] | None = Field(default=None, alias="availability")

    model_config = {"populate_by_name": True}


@router.get("/components")
def get_components(limit: int = 40, session: Session = Depends(get_read_session)):
    """The components a scenario can be run between, richest spend first.

    Only components with both people and closed work appear — anything else
    has no observed rate to forecast from, and offering it in a dropdown
    just to have it fail on selection is worse than not offering it.
    """
    components = list_components(session, limit=limit)
    return {
        "projects": [c.key for c in components],
        "detail": [
            {
                "key": c.key,
                "project": c.repo,
                "component": c.component,
                "engineers": c.n_engineers,
                "openItems": c.open_items,
                "itemsPerWeek": round(c.items_per_week, 3),
                "cost": float(c.cost) if c.cost is not None else None,
                "suppressed": c.cost is None,
            }
            for c in components
        ],
        "assumptions": {"rampUpWeeks": RAMP_WEEKS, "rampUpEfficiency": RAMP_EFFICIENCY},
    }


@router.post("")
def post_scenario(
    body: ScenarioRequest, session: Session = Depends(get_read_session)
):
    """Move N engineers and price both halves of the move."""
    try:
        scenario = Scenario(
            source=load_component(session, body.source_project),
            destination=load_component(session, body.dest_project),
            engineer_count=body.engineer_count,
        )
        result = run(scenario)
    except ScenarioRefused as exc:
        # 422, not 500: the request was well formed, the data cannot answer it.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = {
        "sourceDeltaWeeks": result.source_delta_weeks,
        "destDeltaWeeks": result.dest_delta_weeks,
        "netCostRupees": result.net_cost_rupees,
        "confidenceLow": result.confidence_low,
        "confidenceHigh": result.confidence_high,
        "confidencePercent": result.confidence_percent,
        "rampUpPenaltyApplied": result.ramp_up_penalty_applied,
        "rampUpNote": result.ramp_up_note,
        "priced": result.priced,
        "priceNote": result.price_note,
        "assumptions": result.assumptions,
        "mode": "named" if body.named_recommendations else "capacity",
    }
    if not body.named_recommendations:
        return payload

    # The workforce layer is imported here, not at module scope, so the
    # simulator keeps working if that package is absent or broken. Capacity
    # mode must never fail because the named layer did.
    from app.api.workforce import RecommendRequest, build_recommendations

    payload["workforce"] = build_recommendations(
        RecommendRequest(
            component=body.dest_project,
            engineerCount=body.engineer_count,
            shift=body.shift,
            availability=body.availability,
            # Any source != destination move is cross-team by definition, so
            # somebody who opted out of that is excluded rather than ranked.
            crossTeam=True,
        )
    )
    return payload
