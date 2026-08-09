"""
Workforce router — named, consented recommendations.
Owner: Dipen (models lane; this is the recommendation engine, not ingestion).

THE ONLY ROUTER IN THIS APP THAT RETURNS A NAME. Everything it returns comes
from `app.workforce`, which reads a separate database and cannot see the event
log. No handler here takes a Session against the warehouse, which is why the
"never joined" rule cannot be broken by editing this file.

Recommendations are proposals. Nothing here writes an assignment, and there is
no endpoint that could — `POST /workforce/preferences` records what an employee
said about themselves, and that is the only write in the package.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.workforce import matching, requirements, store

router = APIRouter(prefix="/workforce", tags=["workforce"])

#: Returned with every recommendation set. The human stays responsible.
HUMAN_IN_THE_LOOP = {
    "decision": "recommendation",
    "note": (
        "A proposed allocation for a human to review. Nothing here has been "
        "applied, and no endpoint in this service can apply it."
    ),
    "actions": ["Review recommendation", "Accept proposed scenario"],
}

#: Printed on the Workforce and Simulator screens. Per route, not global —
#: "no per-person view anywhere" is true of the analytics screens and would be
#: a visible lie here, so this surface states its own basis.
PRIVACY_BASIS = (
    "Named from data the employee volunteered — a preference form and a "
    "resume. Nobody without a submitted preference record is named anywhere "
    "in this response. No observed telemetry, cost or productivity figure is "
    "used to rank anyone."
)


class RecommendRequest(BaseModel):
    component: str = Field(alias="component")
    engineer_count: int = Field(alias="engineerCount", ge=1, le=50)
    shift: str = Field(default="flexible", alias="shift")
    availability: list[str] | None = Field(default=None, alias="availability")
    cross_team: bool = Field(default=True, alias="crossTeam")

    model_config = {"populate_by_name": True}


class PreferencesRequest(BaseModel):
    employee_id: str = Field(alias="employeeId")
    preferred_shift: str = Field(alias="preferredShift")
    work_areas: list[str] = Field(default_factory=list, alias="workAreas")
    availability: list[str] = Field(default_factory=list, alias="availability")
    work_style: str = Field(default="mixed", alias="workStyle")
    open_to_other_teams: bool = Field(default=True, alias="openToOtherTeams")

    model_config = {"populate_by_name": True}


def serialise_fit(fit: matching.Fit) -> dict[str, Any]:
    """The named payload.

    EVERY KEY HERE IS DECLARED OR RESUME-DERIVED. There is deliberately no
    cycle time, throughput, review count, items merged, capability index or
    rank-against-others field, and `test_workforce.py` asserts their absence
    rather than trusting this comment.
    """
    return {
        "employeeId": fit.employee_id,
        "name": fit.name,
        "matchScore": fit.match_score,
        "matchPercent": round(fit.match_score * 100),
        "subScores": fit.sub_scores,
        "contributions": fit.contributions,
        "skills": list(fit.skills),
        "matchedSkills": list(fit.matched_skills),
        "missingSkills": list(fit.missing_skills),
        "reasons": list(fit.reasons),
        "flags": list(fit.flags),
        "evidence": fit.evidence,
    }


def serialise_requirement(req: requirements.Requirement) -> dict[str, Any]:
    return {
        "project": req.project,
        "component": req.component,
        "engineersRequired": req.engineers_required,
        "requiredSkills": list(req.required_skills),
        "workAreas": list(req.work_areas),
        "preferredShift": req.preferred_shift,
        "requiredAvailability": list(req.required_availability),
        "thin": req.thin,
        "basis": req.basis,
    }


def build_recommendations(body: RecommendRequest) -> dict[str, Any]:
    """Shared by this router and the simulator's named mode."""
    req = requirements.derive(
        component_key=body.component,
        engineers_required=body.engineer_count,
        shift=body.shift,
        availability=tuple(body.availability) if body.availability else None,
    )
    candidates = store.named_candidates()
    fits, excluded = matching.rank(candidates, req, cross_team=body.cross_team)

    top = fits[: body.engineer_count]
    alternates = fits[body.engineer_count : body.engineer_count + 3]
    return {
        "requirement": serialise_requirement(req),
        "recommendedEmployees": [serialise_fit(f) for f in top],
        "alternates": [serialise_fit(f) for f in alternates],
        "excluded": [
            {"employeeId": e.employee_id, "name": e.name, "reason": e.reason}
            for e in excluded
        ],
        "anonymousCapacity": {
            "count": store.anonymous_capacity(),
            "note": (
                "Profiles with no submitted preference record. They are modelled "
                "as capacity by the simulator exactly as before and are never "
                "named here, whatever their resume says."
            ),
        },
        "weights": matching.WEIGHTS,
        "explanationMethod": (
            "Weighted sum. `contributions` is weight x sub-score per dimension "
            "and sums to matchScore, so the arithmetic is the explanation."
        ),
        "dataBasis": store.DATA_BASIS,
        "privacyBasis": PRIVACY_BASIS,
        "humanInTheLoop": HUMAN_IN_THE_LOOP,
    }


@router.get("/requirement")
def get_requirement(component: str, engineerCount: int = 2, shift: str = "flexible"):
    """What the opening needs, without scoring anybody."""
    return serialise_requirement(
        requirements.derive(component, engineerCount, shift)
    )


@router.post("/recommend")
def post_recommend(body: RecommendRequest):
    """Rank consented employees against a scenario. Names, scores, reasons."""
    return build_recommendations(body)


@router.post("/preferences")
def post_preferences(body: PreferencesRequest):
    """Record a submission — the only way somebody becomes nameable."""
    try:
        saved_at = store.save_preferences(
            body.employee_id,
            {
                "employeeId": body.employee_id,
                "preferredShift": body.preferred_shift,
                "workAreas": body.work_areas,
                "availability": body.availability,
                "workStyle": body.work_style,
                "openToOtherTeams": body.open_to_other_teams,
            },
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"saved": True, "savedAt": saved_at}
