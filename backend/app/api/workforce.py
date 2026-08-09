"""
Workforce router — evidence-grounded staffing recommendations.
Owner: Livana (RAG integration). Employee data/store is Diljit's lane; this
reads it through app/rag/store.py so the two can land independently.
Phase: Tier 3.

    POST /workforce/recommend       who fits an opening, and on what evidence
    POST /workforce/staffing-plan   the same, plus what the move is projected
                                    to cost — the two composed

/recommend takes NO DATABASE SESSION and must not. The recommender reads
volunteered employee data and nothing else; app/rag cannot import a session
at all, and an AST test enforces that.

/staffing-plan does take one, because it also calls the existing simulator,
which reads the pseudonymised views exactly as /simulate does. That is a
composition, not a join: the forecast is driven by headcount and observed
component throughput, and `Scenario` has no field that could carry an
identity, so the projection is the same whoever is recommended. Named
employees and analytics figures sit side by side in one response and are
still computed from separate data by separate code. A test swaps the
employee store and asserts the simulation does not move.

The response carries the retrieved evidence and the score breakdown, not
just a number. A ranked list of people with an unexplained percentage beside
each name is the exact shape of thing a manager should refuse to act on.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_read_session
from app.models.simulator import Scenario, ScenarioRefused, load_component, run
from app.rag.recommend import WEIGHTS, Candidate, recommend

router = APIRouter(prefix="/workforce", tags=["workforce"])


class RecommendRequest(BaseModel):
    project: str | None = None
    component: str | None = None
    required_skills: list[str] = Field(default_factory=list, alias="requiredSkills")
    shift: str | None = None
    availability: list[str] = Field(default_factory=list)
    headcount: int = Field(default=1, ge=1, le=50)

    model_config = {"populate_by_name": True}


class EvidenceOut(BaseModel):
    source: str
    kind: str
    text: str
    #: BM25 relevance of this chunk to the scenario query. Absolute, so it is
    #: comparable within one response and not between responses.
    score: float


class RecommendationOut(BaseModel):
    employee_id: str = Field(serialization_alias="employeeId")
    name: str
    match_score: float = Field(serialization_alias="matchScore")
    skills: list[str]
    missing_skills: list[str] = Field(serialization_alias="missingSkills")
    reasons: list[str]
    score_breakdown: dict[str, float] = Field(serialization_alias="scoreBreakdown")
    evidence: list[EvidenceOut]

    model_config = {"populate_by_name": True}


class RecommendResponse(BaseModel):
    recommendations: list[RecommendationOut]
    query: str
    weights: dict[str, float]
    note: str


def _to_out(c: Candidate) -> RecommendationOut:
    return RecommendationOut(
        employee_id=c.employee.employee_id,
        name=c.employee.name,
        match_score=c.match_score,
        skills=c.matched_skills,
        missing_skills=c.missing_skills,
        reasons=c.reasons,
        score_breakdown=c.breakdown(),
        evidence=[
            EvidenceOut(source=h.source, kind=h.kind, text=h.text, score=h.score)
            for h in c.evidence
        ],
    )


@router.post("/recommend", response_model=RecommendResponse, response_model_by_alias=True)
def post_recommend(req: RecommendRequest) -> RecommendResponse:
    """Rank candidates for one opening, with the evidence behind each.

    Deterministic: the same request against the same store returns the same
    order and the same scores.
    """
    from app.rag.retriever import build_query

    candidates = recommend(
        required_skills=req.required_skills,
        project=req.project,
        component=req.component,
        shift=req.shift,
        availability=req.availability,
        headcount=req.headcount,
    )
    return RecommendResponse(
        recommendations=[_to_out(c) for c in candidates],
        query=build_query(
            req.required_skills, req.project, req.component, req.shift, req.availability
        ),
        weights={
            "skills": WEIGHTS.skills,
            "resume": WEIGHTS.resume,
            "preference": WEIGHTS.preference,
            "availability": WEIGHTS.availability,
            "familiarity": WEIGHTS.familiarity,
        },
        note=(
            "A shortlist for a human to review, not an assignment. Scores are a "
            "stated weighting of evidence the employee volunteered, not a model "
            "output and not a rating of the person."
        ),
    )


# ---------------------------------------------------------------------------
# The composition: scenario -> requirements -> RAG -> simulator
# ---------------------------------------------------------------------------
#
# THE TWO HALVES COMPOSE, THEY DO NOT JOIN, AND THE DIFFERENCE IS THE WHOLE
# PRIVACY ARGUMENT.
#
# The recommender answers WHO, from data employees volunteered. The simulator
# answers WHAT HAPPENS IF N ENGINEERS MOVE, from pseudonymised component
# throughput. It takes a headcount, never an identity: `Scenario` has no field
# that could carry one. So the projection is IDENTICAL whichever candidates
# come back, and a test asserts exactly that by swapping the employee store and
# checking the simulation is unchanged.
#
# Saying it plainly matters, because the natural misreading of this response is
# "moving Employee A produces this cost". It does not. It says: here is who
# fits the opening, and separately, here is what moving this many people costs.
# Presenting a per-person cost is the one thing this product promises it never
# does.
#
# Dipen owns the forecast. Nothing here recomputes, adjusts or second-guesses
# it — `run(Scenario(...))` is called exactly as /simulate calls it and the
# result is passed through under its own field names.


class StaffingPlanRequest(BaseModel):
    """A simulator scenario plus the requirements that describe the opening.

    Field names are taken from the two endpoints this composes rather than
    invented, so a caller that already builds a /simulate body or a
    /workforce/recommend body can reuse it unchanged.
    """

    # --- the simulator's half (names as in ScenarioRequest) ---
    source_project: str = Field(alias="sourceProject")
    dest_project: str = Field(alias="destProject")
    engineer_count: int = Field(alias="engineerCount", ge=1, le=200)

    # --- the recommender's half (names as in RecommendRequest) ---
    required_skills: list[str] = Field(default_factory=list, alias="requiredSkills")
    shift: str | None = None
    availability: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class StaffingPlanResponse(BaseModel):
    recommended_employees: list[RecommendationOut] = Field(
        serialization_alias="recommendedEmployees"
    )
    #: The /simulate response verbatim, under its own field names. Passed
    #: through rather than reshaped so there is one definition of what a
    #: delta means, and it is Dipen's.
    simulation: dict
    query: str
    note: str

    model_config = {"populate_by_name": True}


@router.post("/staffing-plan", response_model=StaffingPlanResponse, response_model_by_alias=True)
def post_staffing_plan(
    req: StaffingPlanRequest, session: Session = Depends(get_read_session)
) -> StaffingPlanResponse:
    """Who fits the opening, and what the move is projected to cost.

    The recommendation half needs no database and the simulation half reads
    only the pseudonymised views, exactly as /simulate does. A refused
    scenario surfaces as a 422 carrying the simulator's own sentence, because
    "we cannot answer that honestly" is the right answer and the caller should
    see the reason rather than a status code.
    """
    from app.rag.retriever import build_query

    # The opening being staffed, in the recommender's terms. Component is the
    # destination: the requirement is for people to work THERE.
    repo, _, component = req.dest_project.rpartition("/")

    candidates = recommend(
        required_skills=req.required_skills,
        project=repo or req.dest_project,
        component=component or None,
        shift=req.shift,
        availability=req.availability,
        headcount=req.engineer_count,
    )

    try:
        result = run(
            Scenario(
                source=load_component(session, req.source_project),
                destination=load_component(session, req.dest_project),
                engineer_count=req.engineer_count,
            )
        )
    except ScenarioRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return StaffingPlanResponse(
        recommended_employees=[_to_out(c) for c in candidates],
        simulation={
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
        },
        query=build_query(
            req.required_skills, repo, component, req.shift, req.availability
        ),
        note=(
            "The candidates and the projection are computed independently. The "
            "forecast is driven by headcount and observed component throughput, "
            "not by which people are recommended, so it is the same whoever "
            "appears above. No per-person cost is produced anywhere."
        ),
    )
