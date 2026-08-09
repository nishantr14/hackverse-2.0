"""
Spend router — cost breakdowns by repo/component/work item.
Owner: Diljit (cost lane). Reads from app/cost/*, never computes cost itself.
Phase: Tier 1.

Determinism discipline: every number returned here must trace back to SQL in
app/cost/. No AI-generated numbers. This router shapes rows; it does not
arithmetic its way to a figure that isn't already in a view.

Reads through get_read_session — the esi_app role, granted on views only, so
the k-anonymity floor in v_spend_by_component cannot be sidestepped from here
even by accident.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.cost.rate_card import RATES_PATH, RateCardError
from app.db.session import get_read_session
from app.waste.common import citation_for

router = APIRouter(prefix="/spend", tags=["spend"])

CASES_SQL = """
SELECT work_item_id, repo, component, author_hours, review_hours, cost, labour_cost
FROM v_case_spend
WHERE cost > 0
ORDER BY cost DESC
LIMIT :limit
"""

BY_COMPONENT_SQL = """
SELECT repo, component, n_actors, suppressed, cost, k_applied
FROM v_spend_by_component
ORDER BY cost DESC NULLS LAST
"""

TOTALS_SQL = """
SELECT basis, count(*), COALESCE(SUM(hours), 0), COALESCE(SUM(cost), 0)
FROM v_case_cost
JOIN LATERAL (SELECT 'all'::text AS basis) b ON TRUE
GROUP BY basis
"""

BASIS_SQL = """
SELECT
    COALESCE(SUM(labour_cost), 0)  AS labour,
    COALESCE(SUM(ci_cost), 0)      AS ci,
    COALESCE(SUM(ai_cost), 0)      AS ai,
    COALESCE(SUM(meeting_cost), 0) AS meeting,
    COALESCE(SUM(total_cost), 0)   AS total,
    COALESCE(SUM(total_hours), 0)  AS hours
FROM v_case_cost
"""


def _citation() -> dict[str, str | None]:
    """The source string renders on screen beside the money. If it is missing
    the caller must be told, not handed an uncited figure."""
    try:
        import yaml

        cfg = yaml.safe_load(RATES_PATH.read_text(encoding="utf-8")) or {}
        return {
            "labour": citation_for(cfg, "rate_card"),
            "ci": citation_for(cfg, "ci_cost"),
            "error": None,
        }
    except (RateCardError, OSError) as exc:
        return {"labour": None, "ci": None, "error": str(exc)}


@router.get("")
def get_spend(
    limit: int = Query(10_000, ge=1, le=50_000),
    session: Session = Depends(get_read_session),
):
    """One row per priced work item — the shape SpendView reduces over.

    `authorHours` and `reviewHours` are an apportionment of inferred session
    hours by each actor's event mix on that case, not two separately measured
    quantities. See migrations/005.
    """
    rows = session.execute(text(CASES_SQL), {"limit": limit}).all()
    return [
        {
            "workItem": r[0],
            "project": r[1],
            "component": r[2],
            "authorHours": float(r[3] or 0),
            "reviewHours": float(r[4] or 0),
            "cost": float(r[5] or 0),
            # Labour only. `cost` also carries meeting, CI and token spend,
            # none of which is an engineer-hour — dividing it by the hours
            # above would put the blended rate above the staff band.
            "labourCost": float(r[6] or 0),
        }
        for r in rows
    ]


@router.get("/by-component")
def get_by_component(session: Session = Depends(get_read_session)):
    """Component totals WITH the k-anonymity floor applied in the view.

    Suppressed rows are returned with a null cost and `suppressed: true`
    rather than dropped, so the UI can say a value was withheld and print
    the threshold that withheld it.
    """
    rows = session.execute(text(BY_COMPONENT_SQL)).all()
    return {
        "rows": [
            {
                "project": r[0],
                "component": r[1],
                "nActors": r[2],
                "suppressed": bool(r[3]),
                "cost": float(r[4]) if r[4] is not None else None,
            }
            for r in rows
        ],
        "kApplied": rows[0][5] if rows else None,
        "suppressedCount": sum(1 for r in rows if r[3]),
    }


@router.get("/summary")
def get_summary(session: Session = Depends(get_read_session)):
    """Headline totals, split by what kind of spend each rupee is.

    The split matters more than the total: labour is inferred from observed
    activity, CI is metered, AI and meetings are modelled. A single number
    would hide that three different epistemic things were added together.
    """
    row = session.execute(text(BASIS_SQL)).one()
    labour, ci, ai, meeting, total, hours = (float(v) for v in row)
    return {
        "totalCost": total,
        "totalHours": hours,
        "byBasis": {
            "labour": {"cost": labour, "kind": "inferred"},
            "ci": {"cost": ci, "kind": "metered"},
            "ai": {"cost": ai, "kind": "modelled"},
            "meeting": {"cost": meeting, "kind": "modelled"},
        },
        "observedShare": (labour + ci) / total if total else 0.0,
        "blendedHourly": labour / hours if hours else 0.0,
        "citation": _citation(),
        "hoursNote": (
            "Engineer hours are inferred from timestamps bracketed by public "
            "artifacts, so they are a LOWER BOUND on real effort — unobserved "
            "work leaves no event. CI runner minutes are excluded from hours; "
            "they are machine time and are priced separately."
        ),
    }
