"""
Process router — the cost-weighted process graph and variants.
Owner: shared (ingestion + cost data, consumed by Livana's ProcessView —
the view that leads the Round 2 demo).
Phase: Tier 0 (this is the end-to-end path the hour-8 gate checks).

Every route reads through get_read_session (esi_app, views only — see
app.waste.discovery / app.waste.variants for the views themselves). No
route here computes anything; it only shapes what those modules return.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_read_session
from app.waste.discovery import load_edges
from app.waste.variants import load_variants, rare_but_costly

router = APIRouter(prefix="/process", tags=["process"])


@router.get("/graph")
def get_graph(repo: str | None = None, session: Session = Depends(get_read_session)):
    edges = load_edges(session, repo)
    nodes = sorted(
        {e.source_activity for e in edges} | {e.target_activity for e in edges}
    )
    ranked_by_cost = any(e.cost_exposure > 0 for e in edges)
    return {
        "nodes": [{"id": n, "label": n.replace("_", " ").title()} for n in nodes],
        "edges": [
            {
                "from": e.source_activity,
                "to": e.target_activity,
                "frequency": e.n_transitions,
                "nCases": e.n_cases,
                "medianGapHours": e.median_gap_hours,
                "costExposure": e.cost_exposure,
                "significant": e.significant,
            }
            for e in edges
        ],
        "edgeWeightBasis": "cost" if ranked_by_cost else "frequency",
        "costNote": None
        if ranked_by_cost
        else "No session-inferred or CI cost has landed yet — ranked by "
        "frequency until config/rates.yaml's rate_card is seeded.",
    }


#: Activities that are not steps in the software process.
#:
#: A meeting is modelled, and it is placed in the timeline by wall clock, so
#: it lands between whatever two real events happened to bracket it. That
#: produces a transition into and out of every activity in the log — and
#: because those transitions are numerous, `review -> meeting` and
#: `meeting -> review` came out as the two most expensive transitions in the
#: whole process, carrying 15% of all cost between them.
#:
#: They are an artifact of interleaving, not a pattern anyone can act on.
#: Meeting cost is real output and is reported on Spend and Waste; it is the
#: claim that meetings are a STEP that does not hold. Pass
#: include_meetings=true to see them anyway.
NON_PROCESS_ACTIVITIES: tuple[str, ...] = ("meeting",)

MAP_EDGES_SQL = """
SELECT source_activity, target_activity, variant_class,
       SUM(n_transitions) AS frequency, SUM(cost_rupees) AS cost_rupees
FROM v_edges_by_variant
-- Cast: Postgres cannot infer a bare NULL parameter's type.
WHERE (CAST(:repo AS TEXT) IS NULL OR repo = CAST(:repo AS TEXT))
  AND (CAST(:keep_all AS BOOLEAN)
       OR (NOT source_activity = ANY(:excluded)
           AND NOT target_activity = ANY(:excluded)))
GROUP BY 1, 2, 3
ORDER BY 5 DESC
"""

MAP_SUMMARY_SQL = """
SELECT variant_class, share_of_work_items, share_of_cost, n_cases, total_cost
FROM v_variant_class_summary
ORDER BY share_of_cost DESC
"""


@router.get("/map")
def get_map(
    repo: str | None = None,
    limit: int = 20,
    include_meetings: bool = False,
    session: Session = Depends(get_read_session),
):
    """The graph collapsed into the three semantic variant classes.

    This is the drawable form. /graph and /variants stay as they are — the
    true per-sequence variants are the honest process-mining answer, and
    there are thousands of them, which is exactly why they cannot be a
    picture. See migrations/006 for how a case is classified.

    FILTERED BY COST, AND IT SAYS SO. Fifteen activities produce 168 distinct
    transitions; drawn all at once they are a hairball in which nothing is
    legible and therefore nothing is true. The top 20 by cost carry ~81% of
    the money across 10 activities, which is a map someone can read and act
    on. `coverage` reports exactly what share is on screen so the filtering
    is stated rather than hidden, and `limit` raises it.
    """
    params = {
        "repo": repo,
        "keep_all": include_meetings,
        "excluded": list(NON_PROCESS_ACTIVITIES),
    }
    all_edges = session.execute(text(MAP_EDGES_SQL), params).all()
    summary = session.execute(text(MAP_SUMMARY_SQL)).all()

    # Rank by the transition's total cost across variants, so a transition is
    # never half-drawn — keeping one variant's slice of an edge while
    # dropping another would misattribute the line's colour.
    by_transition: dict[tuple[str, str], float] = {}
    for e in all_edges:
        by_transition[(e[0], e[1])] = by_transition.get((e[0], e[1]), 0.0) + float(
            e[4] or 0
        )
    ranked = sorted(by_transition, key=lambda k: by_transition[k], reverse=True)
    kept = set(ranked[:limit])

    edges = [e for e in all_edges if (e[0], e[1]) in kept]
    total_cost = sum(by_transition.values())
    shown_cost = sum(by_transition[k] for k in kept)

    # Only nodes that survived. An activity floating with no edges is a box
    # the eye has to rule out, which is a cost with no information behind it.
    nodes = sorted({e[0] for e in edges} | {e[1] for e in edges})
    return {
        "nodes": [{"id": n, "label": n.replace("_", " ").title()} for n in nodes],
        "edges": [
            {
                "from": e[0],
                "to": e[1],
                "variant": e[2],
                "frequency": int(e[3]),
                "costRupees": float(e[4] or 0),
            }
            for e in edges
        ],
        "coverage": {
            "transitionsShown": len(kept),
            "transitionsTotal": len(by_transition),
            "costShare": shown_cost / total_cost if total_cost else 0.0,
            "meetingsExcluded": not include_meetings,
            "note": (
                "Meetings are modelled and placed by wall clock, so they land "
                "between whichever two real events bracket them and appear to "
                "follow everything. They are priced on Spend and Waste; they "
                "are not a step here."
            )
            if not include_meetings
            else None,
        },
        "variantSummary": [
            {
                "variant": s[0],
                "shareOfWorkItems": float(s[1] or 0),
                "shareOfCost": float(s[2] or 0),
                "nCases": int(s[3]),
                "totalCost": float(s[4] or 0),
            }
            for s in summary
        ],
    }


@router.get("/variants")
def get_variants(repo: str | None = None, session: Session = Depends(get_read_session)):
    variants = load_variants(session, repo)
    modal = next((v for v in variants if v.is_modal), None)
    return {
        "variants": [
            {
                "variantId": v.variant_id,
                "repo": v.repo,
                "activitySequence": v.activity_sequence,
                "nCases": v.n_cases,
                "totalCost": v.total_cost,
                "costSharePct": v.cost_share_pct,
                "isModal": v.is_modal,
            }
            for v in variants
        ],
        "modalVariantId": modal.variant_id if modal else None,
        "rareButCostly": [v.variant_id for v in rare_but_costly(variants)],
    }
